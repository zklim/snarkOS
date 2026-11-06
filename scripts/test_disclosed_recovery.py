#!/usr/bin/env python3
"""Transaction-bearing local recovery evaluation; never contacts a live network."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from recovery_checkpoint import decode_checkpoint
from recovery_resume import digest, read_cache, restore_cache


class PacedHttp:
    """Space requests per endpoint and forward each request exactly once."""

    def __init__(self, opener, interval=.2, clock=time.monotonic, sleep=time.sleep):
        self.opener, self.interval, self.clock, self.sleep = opener, interval, clock, sleep
        self.state_lock = threading.Lock()
        self.endpoints = {}

    def open(self, address, timeout):
        url = address.full_url if isinstance(address, urllib.request.Request) else address
        parsed = urllib.parse.urlsplit(url)
        endpoint = (parsed.scheme, parsed.netloc)
        with self.state_lock:
            state = self.endpoints.setdefault(endpoint, [threading.Lock(), None])
        with state[0]:
            now = self.clock()
            if state[1] is not None and state[1] > now:
                self.sleep(state[1] - now)
            state[1] = self.clock() + self.interval
        return self.opener.open(address, timeout=timeout)


def read_json(http, address, sleep=time.sleep):
    """Retry only rate-limited reads, with a bounded server-directed delay."""
    for attempt in range(6):
        try:
            with http.open(address, timeout=5) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 5:
                raise
            try:
                delay = float(error.headers.get('Retry-After', '1'))
            except (ValueError, TypeError):
                delay = 1
            sleep(min(5, max(.25, delay)))


def accepted_height(expected, confirmed, block):
    """A block's aborted-ID index does not establish transaction acceptance."""
    if confirmed is None or confirmed.get('status') != 'accepted':
        return None
    assert confirmed['type'] == expected['type']
    assert confirmed['transaction'] == expected, 'Confirmed effects differ from submitted transaction'
    matches = [entry for entry in block['transactions'] if entry['transaction']['id'] == expected['id']]
    assert matches == [confirmed], 'Accepted confirmation is absent or duplicated in the block body'
    assert expected['id'] not in block['aborted_transaction_ids']
    return block['header']['metadata']['height']


def accepted_location(expected, query):
    block_hash = query(f'find/blockHash/{expected["id"]}')
    if block_hash is None:
        return None
    block = query(f'block/{block_hash}')
    accepted = [entry for entry in block['transactions']
                if entry['status'] == 'accepted' and entry['transaction']['id'] == expected['id']]
    if not accepted:
        return None
    confirmed = query(f'transaction/confirmed/{expected["id"]}')
    assert confirmed is not None, 'Accepted block entry has no stored confirmation'
    return accepted_height(expected, confirmed, block)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=pathlib.Path)
    parser.add_argument('--build-manifest', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path)
    parser.add_argument('--h1', type=int, default=60)
    parser.add_argument('--h2', type=int, default=120)
    parser.add_argument('--network', type=int, choices=[0, 1, 2], default=1)
    parser.add_argument('--genesis-file', type=pathlib.Path)
    parser.add_argument('--genesis-hash')
    parser.add_argument('--skip-legacy-proof', action='store_true')
    parser.add_argument('--legacy-cache', type=pathlib.Path,
                        help='Verified offline node-specific snapshot and historical proofs from recovery_resume.py')
    parser.add_argument('--client-relay', action='store_true',
                        help='After core validation, relay a native spend through two synchronized clients')
    parser.add_argument('--resume-relay', action='store_true',
                        help='Revalidate retained post-rollback state and continue client relay without rebuilding proofs')
    args = parser.parse_args()
    if args.resume_relay and (args.legacy_cache or not args.client_relay or args.output is None):
        parser.error('--resume-relay requires existing --output and --client-relay, without --legacy-cache')
    if not 22 < args.h1 < args.h2:
        parser.error('Require 22 < h1 < h2')
    if bool(args.genesis_file) != bool(args.genesis_hash):
        parser.error('--genesis-file and --genesis-hash must be supplied together')
    if not args.skip_legacy_proof and not args.genesis_file:
        parser.error('Full historical evaluation requires the pinned V0 --genesis-file and --genesis-hash')
    if args.legacy_cache and args.skip_legacy_proof:
        parser.error('--legacy-cache requires full historical evaluation')
    root = pathlib.Path(__file__).resolve().parents[1]
    binary = (args.binary or root / 'target/debug/snarkos').resolve()
    out = (args.output or pathlib.Path(tempfile.mkdtemp(prefix='snarkos-disclosed-recovery-'))).resolve()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    run = out / 'nodes'
    run.mkdir(exist_ok=True)
    network = {0: 'mainnet', 1: 'testnet', 2: 'canary'}[args.network]
    schedule = ','.join(map(str, [*range(22), args.h1, args.h2]))
    cache = None
    if args.legacy_cache:
        assert not any(run.iterdir()), 'Cached evaluation requires a fresh output directory'
        cache = read_cache(args.legacy_cache)
        assert cache['network'] == network and cache['schedule'] == schedule
        assert cache['genesis_hash'] == args.genesis_hash
        assert cache['genesis_sha256'] == digest(args.genesis_file)
        restore_cache(args.legacy_cache, run)
    env = dict(os.environ, CONSENSUS_VERSION_HEIGHTS=schedule, RAYON_NUM_THREADS='2', TOKIO_WORKER_THREADS='2')
    env.pop('TEST_BOOTSTRAP_PEERS', None)
    http = PacedHttp(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    processes, streams = {}, {}
    prior = None
    if args.resume_relay:
        prior_path = out / 'results.json'
        prior = json.loads(prior_path.read_text())
        assert prior['phases']['rollback-replayed']['status'] == 'passed'
        assert prior['network'] == network and prior['schedule'] == schedule
        assert prior['genesis_hash'] == args.genesis_hash and prior['genesis_sha256'] == digest(args.genesis_file)
        assert prior['binary_sha256'] == digest(binary)
        backup = out / 'results-before-relay-resume.json'
        assert not backup.exists(), 'Inspect an existing continuation before another attempt'
        shutil.copyfile(prior_path, backup)
        prior.setdefault('continuations', []).append({'prior_results_sha256': digest(backup),
            'prior_status': prior['status'], 'prior_error': prior.get('error'),
            'harness_sha256': digest(pathlib.Path(__file__)),
            'reason': 'Revalidate completed snapshot replay and finish relay after a rate-limited read'})
        prior['status'] = 'running'
        prior.pop('error', None)
    results = prior if prior is not None else {
        'status': 'running', 'network': network, 'schedule': schedule,
        'binary': subprocess.check_output([str(binary), '--version'], text=True).strip(),
        'binary_sha256': hashlib.file_digest(binary.open('rb'), 'sha256').hexdigest(),
        'source_commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'source_working_tree_dirty': bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip()),
        'tracked_patch_sha256': hashlib.sha256(subprocess.check_output(['git', '-C', str(root), 'diff', '--binary', 'HEAD'])).hexdigest(),
        'harness_sha256': hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        'checkpoint_decoder_sha256': hashlib.sha256((pathlib.Path(__file__).parent / 'recovery_checkpoint.py').read_bytes()).hexdigest(),
        'genesis_file': str(args.genesis_file) if args.genesis_file else None,
        'genesis_hash': args.genesis_hash,
        'limits': ['Local development validator keys and disclosed records only',
                   'Classical BFT consensus and historical state remain trusted',
                   'Topology partition uses persisted process restarts and explicit trusted peers',
                   'Historical replay uses known canonical classical devnet history; it does not demonstrate quantum-safe bootstrap',
                   'After legacy compromise, bootstrapping needs an authentic checkpoint including pre-compromise V2 enrollment plus continuing authenticated state',
                   'No finalized BFT reorganization or live network deployment'],
        'commands': [], 'transactions': {}, 'negative_admission': {}, 'phases': {},
    }
    results['http_pacing'] = {'minimum_interval_seconds': http.interval,
                              'scope': 'per scheme and endpoint; shared by reads and broadcasts',
                              'post_retries': False}
    if cache:
        results['legacy_cache'] = {'manifest': str(args.legacy_cache / 'legacy-cache.json'),
            'manifest_sha256': digest(args.legacy_cache / 'legacy-cache.json'),
            'original_binary_sha256': cache['original_binary_sha256'],
            'anchor': cache['anchor'], 'proofs': cache['proofs']}
    identities = {}
    for filename in ['test_disclosed_recovery.py', 'recovery_checkpoint.py', 'recovery_resume.py']:
        source = pathlib.Path(__file__).parent / filename
        retained = out / f'used-{"continuation-" if args.resume_relay else ""}{filename}'
        shutil.copyfile(source, retained)
        identities[filename] = {'sha256': digest(source), 'snapshot': str(retained)}
    (out / ('continuation-runner-source-identities.json' if args.resume_relay else 'runner-source-identities.json')).write_text(json.dumps(identities, indent=2) + '\n')
    if args.genesis_file:
        results['genesis_sha256'] = hashlib.sha256(args.genesis_file.read_bytes()).hexdigest()
    if args.build_manifest:
        build_bytes = args.build_manifest.read_bytes()
        build_identity = json.loads(build_bytes)
        assert build_identity['binary_sha256'] == results['binary_sha256'], 'Build manifest binary mismatch'
        assert build_identity['unchanged_build_inputs'] and build_identity['matches_candidate_compile_inputs']
        results['build_identity'] = {'manifest': str(args.build_manifest),
            'manifest_sha256': hashlib.sha256(build_bytes).hexdigest(),
            'candidate_manifest_sha256': build_identity['candidate_manifest_sha256'],
            'compile_inputs': {name: identity['inputs_sha256'] for name, identity in build_identity['repositories'].items()}}
    print(f'Evidence: {out}', flush=True)

    def save():
        (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')

    def url(i, route):
        return f'http://127.0.0.1:{3030+i}/v2/{network}/{route}'

    def get(i, route):
        return read_json(http, url(i, route))

    def maybe_get(i, route):
        try:
            return get(i, route)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def post(i, path):
        request = urllib.request.Request(url(i, 'transaction/broadcast?check_transaction=true'), data=path.read_bytes(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
        started = time.monotonic()
        try:
            with http.open(request, timeout=90) as response:
                return {'status': response.status, 'body': response.read().decode(),
                        'seconds': time.monotonic() - started}
        except urllib.error.HTTPError as error:
            return {'status': error.code, 'body': error.read().decode(),
                    'seconds': time.monotonic() - started}

    def heads(indices=range(4)):
        return [get(i, 'block/height/latest') for i in indices]

    def wait(predicate, description, timeout=900, allow_exit=()):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            for i, process in processes.items():
                if i not in allow_exit and process.poll() is not None:
                    raise RuntimeError(f'Node {i} exited {process.returncode}; inspect {run}')
            try:
                value = predicate()
                if value:
                    print(description, value, flush=True)
                    return value
            except (OSError, ValueError) as error:
                last = str(error)
            time.sleep(0.25)
        raise TimeoutError(f'{description}: {last}')

    def stable_heads(indices=range(4), seconds=5):
        previous = None
        stable_since = time.monotonic()

        def observed():
            nonlocal previous, stable_since
            current = heads(indices)
            if current != previous:
                previous, stable_since = current, time.monotonic()
            return current if time.monotonic() - stable_since >= seconds else None

        return wait(observed, 'Observed stable paused heads')

    def start(i, group=None, fault_height=None, client=False, p2p_peers=None):
        peers = range(4) if group is None else group
        validators = ','.join(f'127.0.0.1:{5000+j}' for j in peers if j != i)
        log = run / f'node-{i}-{time.time_ns()}.log'
        command = [str(binary), '--noupdater', 'start', '--client' if client else '--validator', '--dev', str(i),
                   '--network', str(args.network), '--dev-num-validators', '4', '--nodisplay', '--nobanner',
                   '--nocdn', '--no-dev-txs', '--peers', p2p_peers or '',
                   '--node', f'127.0.0.1:{4130+i}',
                   '--rest', f'127.0.0.1:{3030+i}', '--nojwt', '--ledger-storage', str(run / f'ledger-{i}'),
                   '--node-data-storage', str(run / f'data-{i}'), '--logfile', str(log) + '.internal']
        if not client:
            command.extend(['--validators', validators, '--bft', f'127.0.0.1:{5000+i}'])
        if group is not None or p2p_peers is not None:
            command.append('--trusted-peers-only')
        if args.genesis_file:
            command.extend(['--dev-genesis-file', str(args.genesis_file), '--dev-genesis-hash', args.genesis_hash])
        node_env = dict(env)
        if fault_height is not None:
            node_env['SNARKVM_RECOVERY_EXIT_BEFORE_COMMIT_HEIGHT'] = str(fault_height)
        stream = log.open('w')
        processes[i] = subprocess.Popen(command, cwd=root, env=node_env, stdout=stream, stderr=subprocess.STDOUT)
        streams[i] = stream
        results['commands'].append({'command': command, 'log': str(log), 'fault_height': fault_height})

    def stop(i):
        process = processes.pop(i, None)
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                results.setdefault('forced_stops', []).append(i)
        stream = streams.pop(i, None)
        if stream:
            stream.close()

    def stop_all():
        for i in list(processes):
            stop(i)

    def command(name, arguments, timeout=300):
        invocation = [str(binary), '--noupdater', 'developer', '--network', str(args.network), 'recovery', *arguments]
        started = time.monotonic()
        completed = subprocess.run(invocation, cwd=root, env=env, text=True, capture_output=True, timeout=timeout)
        (out / f'{name}.stdout').write_text(completed.stdout)
        (out / f'{name}.stderr').write_text(completed.stderr)
        results['commands'].append({'command': invocation, 'returncode': completed.returncode,
                                    'seconds': time.monotonic() - started, 'stdout': f'{name}.stdout'})
        save()
        if completed.returncode:
            raise RuntimeError(f'{name} failed: {completed.stderr[-3000:]} {completed.stdout[-1000:]}')
        lines = [line for line in completed.stdout.splitlines() if line.startswith('{')]
        return json.loads(lines[-1])

    def snapshot(name):
        state = get(0, 'recovery/state')
        path = out / f'{name}-state.json'
        path.write_text(json.dumps(state, indent=2) + '\n')
        return path

    def build(name, action, key, input_id=None, source=None, start_height=None, recipient=None, state=None, replay=None):
        path = out / f'{name}.json'
        options = ['build', action, '--key-file', str(out / f'{key}.key.json'), '--store', str(path),
                   '--endpoint', 'http://127.0.0.1:3030', '--fee', '100000']
        if input_id:
            options.extend(['--input', input_id])
        if source:
            options.extend(['--source-file', str(source), '--dev-key', '0'])
        if start_height is not None:
            options.extend(['--valid-from', str(start_height), '--valid-until', str(start_height + 200)])
        if recipient:
            options.extend(['--recipient-key-file', str(out / f'{recipient}.key.json')])
        if state:
            options.extend(['--state-file', str(state)])
        if replay:
            options.extend(['--test-replay', replay])
        metadata = command(name, options)
        metadata['file'] = str(path)
        metadata['json_bytes'] = path.stat().st_size
        results['transactions'][name] = metadata
        save()
        return metadata

    def validation_rejection(response):
        if response['status'] != 422:
            return False
        body = json.loads(response['body'])
        return body.get('error_type') == 'unprocessable_entity' and body.get('message') == 'Invalid transaction' and bool(body.get('chain'))

    def reject(name, path):
        responses = [post(i, path) for i in range(4)]
        results['negative_admission'][name] = responses
        save()
        assert all(validation_rejection(response) for response in responses), (name, responses)

    def submit(name, transaction, node=0):
        response = post(node, pathlib.Path(transaction['file']))
        results['transactions'][name]['admission'] = response
        save()
        assert 200 <= response['status'] < 300, (name, response)

    def location(i, transaction):
        expected = json.loads(pathlib.Path(transaction['file']).read_text())
        assert expected['id'] == transaction['transaction_id']
        return accepted_location(expected, lambda route: maybe_get(i, route))

    def included(name, transaction, minimum=None, maximum=None, indices=range(4)):
        def finalized_heights():
            values = [location(i, transaction) for i in indices]
            return values if all(value is not None for value in values) else None

        observed = wait(finalized_heights, f'{name} finalized')
        assert len(set(observed)) == 1, observed
        if minimum is not None:
            assert observed[0] >= minimum, (name, observed)
        if maximum is not None:
            assert observed[0] < maximum, (name, observed)
        results['transactions'][name]['accepted_height'] = observed[0]
        results['transactions'][name]['confirmed'] = [get(i, f'transaction/confirmed/{transaction["transaction_id"]}') for i in indices]
        save()
        return observed[0]

    pool_route = 'program/credits.aleo/mapping/pool/aleo1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3ljyzc'

    def pool(i):
        value = maybe_get(i, pool_route)
        return 0 if value is None else int(re.fullmatch(r'(\d+)u64', value).group(1))

    def source_amount(path):
        record = json.loads(pathlib.Path(path).read_text())['record']
        return int(re.search(r'microcredits:\s*(\d+)u64', record).group(1))

    def accepted_accounting():
        accepted = {name: tx for name, tx in results['transactions'].items() if 'accepted_height' in tx and 'output_amount' in tx}
        outputs = {tx['output']: tx['output_amount'] for tx in accepted.values()}
        consumed = [tx['input'] for tx in accepted.values()]
        assert len(set(consumed)) == len(consumed)
        assert len(outputs) == len(accepted)
        remaining = {identifier: amount for identifier, amount in outputs.items() if identifier not in consumed}
        prepared = sum(tx['output_amount'] + tx['fee'] for name, tx in accepted.items() if name.startswith('prepare-'))
        fees = sum(tx['fee'] for tx in accepted.values())
        assert prepared == results['prepared_source_microcredits'] == sum(remaining.values()) + fees
        return {'prepared_microcredits': prepared, 'native_base_fees': fees,
                'unspent_microcredits': sum(remaining.values()), 'unspent_outputs': remaining,
                'consumed_ids': sorted(consumed), 'record_ids': sorted(set(outputs) | set(consumed)),
                'accepted_native_transactions': len(accepted)}

    def same_state(name, indices=range(4)):
        def consistent_states():
            states = [get(i, 'recovery/state') for i in indices]
            return states if len({state['root'] for state in states}) == 1 else None

        states = wait(consistent_states, name)
        assert len(set(s['checkpoint'] for s in states)) == 1
        decoded = decode_checkpoint(states[0])
        accounting = accepted_accounting()
        assert decoded['unspent_outputs'] == accounting['unspent_outputs']
        assert decoded['fees'] == accounting['native_base_fees']
        assert decoded['spent_ids'] == accounting['consumed_ids']
        assert sorted(decoded['records']) == accounting['record_ids']
        assert len(decoded['replay_ids']) == accounting['accepted_native_transactions']
        (out / f'{name}-decoded-checkpoint.json').write_text(json.dumps(decoded, indent=2) + '\n')
        for i, state in zip(indices, states):
            (out / f'{name}-node-{i}-state.json').write_text(json.dumps(state, indent=2) + '\n')
        pools = [pool(i) for i in indices]
        assert pools == [results['expected_historical_pool']] * len(states), pools
        results['phases'][name] = {'status': 'passed', 'heads': heads(indices), 'root': states[0]['root'],
                                  'historical_credit_pools': pools, 'accounting': accounting,
                                  'state_comparison': 'Independent Python checkpoint decode matches finalized client effects and disclosed source amounts'}
        save()
        return states[0]['root']

    def topology(groups):
        observed = [get(i, 'connections/bft/all') for i in range(4)]
        for i, peers in enumerate(observed):
            addresses = {peer if isinstance(peer, str) else peer['listener_addr'] for peer in peers}
            expected = {f'127.0.0.1:{5000+j}' for j in groups[i] if j != i}
            if addresses != expected:
                return None
        return observed

    def capture_rollback():
        height = get(0, 'block/height/latest')
        block_hash = get(0, f'block/{height}')['block_hash']
        state_root = get(0, 'recovery/state')['root']
        stop(0)
        directory = out / 'rollback-before-h1'
        directory.mkdir()
        for name in ['ledger-0', 'data-0']:
            shutil.copytree(run / name, directory / name)
        results['phases']['rollback-checkpoint'] = {'minimum_height': height,
            'before_activation': args.h1, 'block_hash': block_hash, 'root': state_root,
            'method': 'Offline full ledger and BFT snapshot before native preparation'}
        return height, block_hash, directory

    try:
        if args.resume_relay:
            for i in range(4):
                start(i)
            wait(lambda: len(heads()) == 4, 'Retained validators ready')
            reconnected_root = results['phases']['reconnected-transactions']['root']
            resumed = results['transactions']['reconnected-spend']
            assert same_state('resumed-core-validation') == reconnected_root
            for name, transaction in results['transactions'].items():
                if 'accepted_height' in transaction:
                    expected_height = transaction['accepted_height']
                    assert all(location(i, transaction) == expected_height for i in range(4)), name
            checkpoint = results['phases']['rollback-checkpoint']
            assert all(get(i, f'block/{checkpoint["minimum_height"]}')['block_hash'] == checkpoint['block_hash'] for i in range(4))
            for height, hashes in results['phases']['activations']['boundary_hashes'].items():
                assert [get(i, f'block/{height}')['block_hash'] for i in range(4)] == hashes
            results['phases']['rollback-replayed']['method'] = 'Offline full-state restore and replay; exact accepted bodies and boundary hashes revalidated on all four nodes'
            results['phases']['accepted-effects-conservation'] = {'status': 'passed', **accepted_accounting()}
            results['core_status'] = 'passed'
            save()
        else:
            for i in range(4):
                start(i)
            wait(lambda: len(heads()) == 4, 'Four validators ready')
            results['genesis_blocks'] = [get(i, 'block/0') for i in range(4)]
            assert len(set(block['block_hash'] for block in results['genesis_blocks'])) == 1
            versions = [get(i, 'version') for i in range(4)]
            assert all(version['consensus_heights'][-2:] == [args.h1, args.h2] for version in versions)
            if cache:
                anchor = cache['anchor']
                assert max(heads()) < args.h1 - 1
                assert all(get(i, f'block/{anchor["minimum_height"]}')["block_hash"] == anchor['block_hash'] for i in range(4))
                assert all(get(i, 'recovery/state')['root'] == anchor['root'] for i in range(4))
                results['phases']['cache-restored'] = {'status': 'passed', 'heads': heads(),
                    'anchor_height': anchor['minimum_height'], 'anchor_hash': anchor['block_hash'],
                    'method': 'Each validator restored its own offline ledger and BFT cache; historical proofs require fresh verification'}
            sources = command('sources', ['sources', '--dev-key', '0', '--output-dir', str(out / 'sources'),
                                          '--endpoint', 'http://127.0.0.1:3030'])['sources']
            assert len(sources) >= 4, sources
            results['historical_sources'] = sources
            if args.genesis_file:
                assert all(source['version'] == '0u8' for source in sources[:4]), sources
            for name, algorithm in [('ml', 'ml-dsa65'), ('falcon', 'falcon512'), ('rotated', 'ml-dsa65'), ('attacker', 'ml-dsa65')]:
                command(f'keygen-{name}', ['keygen', '--algorithm', algorithm, '--key-file', str(out / f'{name}.key.json')])
            before = snapshot('before-h1')

            def consistent_pools():
                values = [pool(i) for i in range(4)]
                return values if len(set(values)) == 1 else None

            results['historical_pool_before'] = wait(consistent_pools, 'Consistent initial historical pool')
            if cache:
                results['historical_pool_at_resume'] = results['historical_pool_before']
                results['historical_pool_before'] = cache['historical_pool_before']
                baseline = cache['historical_pool_before'][0]
                assert results['historical_pool_at_resume'][0] in [baseline, baseline + source_amount(sources[3]['path'])]
            preparation = build('prepare-ml', 'prepare', 'ml', source=sources[0]['path'], start_height=args.h1, state=before)
            competing = build('prepare-competing', 'prepare', 'ml', source=sources[0]['path'], start_height=args.h1, state=before)
            falcon = build('prepare-falcon', 'prepare', 'falcon', source=sources[1]['path'], start_height=args.h1,
                           state=before, replay=preparation['replay'])
            rotation_source = sources[2]['path']
            dormant_source = sources[3]['path']
            if not args.skip_legacy_proof:
                wait(lambda: min(heads()) >= 22, 'Stable legacy consensus profile')
                for i in [1, 2, 3]:
                    stop(i)
                proving_height = stable_heads([0])[0]

                def legacy_proof(name, source_path):
                    source = json.loads(pathlib.Path(source_path).read_text())
                    path = out / f'{name}.json'
                    if cache:
                        proof = cache['proofs'][name]
                        assert source == json.loads((args.legacy_cache / proof['source_file']).read_text())
                        assert sources[proof['source_index']]['commitment'] == proof['source_commitment']
                        shutil.copyfile(args.legacy_cache / proof['file'], path)
                        assert digest(path) == proof['sha256']
                        results['phases'][f'proof-{name}'] = {'status': 'passed', 'reused_proof': True,
                            'original_generation_seconds': proof['original_generation_seconds'],
                            'original_binary_sha256': cache['original_binary_sha256'],
                            'sha256': proof['sha256'], 'transaction_file': str(path),
                            'verification': 'Generation cached only; current validator verification follows'}
                        save()
                        return path
                    invocation = [str(binary), '--noupdater', 'developer', '--network', str(args.network), 'execute', '--dev-key', '0',
                                  '--endpoint', 'http://127.0.0.1:3030', '--store', str(path), '--store-format', 'string',
                                  'credits.aleo', 'upgrade', source['record']]
                    started = time.monotonic()
                    results['phases'][f'proof-{name}'] = {'status': 'running', 'paused_height': proving_height}
                    save()
                    completed = subprocess.run(invocation, cwd=root, env=env, text=True, capture_output=True, timeout=1800)
                    (out / f'{name}.stdout').write_text(completed.stdout)
                    (out / f'{name}.stderr').write_text(completed.stderr)
                    results['commands'].append({'command': invocation, 'returncode': completed.returncode,
                                                'seconds': time.monotonic() - started,
                                                'json_bytes': path.stat().st_size if path.exists() else None})
                    results['phases'][f'proof-{name}'] = {'status': 'passed' if completed.returncode == 0 else 'failed',
                        'paused_height': proving_height, 'seconds': time.monotonic() - started,
                        'transaction_file': str(path)}
                    save()
                    assert completed.returncode == 0, completed.stderr[-3000:]
                    return path

                legacy_path = legacy_proof('legacy-competing-upgrade', sources[0]['path'])
                dormant_legacy_path = legacy_proof('legacy-dormant-upgrade', sources[2]['path'])
                accepted_legacy = legacy_proof('legacy-v0-to-v1', sources[3]['path'])
                assert get(0, 'block/height/latest') == proving_height, 'One validator advanced without a quorum'
                results['phases']['legacy-proving-quorum-pause'] = {'status': 'passed',
                    'online_validators': [0], 'height_before': proving_height,
                    'height_after': get(0, 'block/height/latest'),
                    'purpose': 'Construct classical proofs before activation without a wall-clock race'}
                rollback_height, rollback_hash, rollback = capture_rollback()
                start(0)
                wait(lambda: get(0, 'block/height/latest') == rollback_height, 'Legacy verification validator ready', allow_exit=[1, 2, 3])
                checked = {name: post(0, path) for name, path in [('competing', legacy_path), ('dormant', dormant_legacy_path)]}
                assert all(200 <= response['status'] < 300 for response in checked.values()), checked
                assert get(0, 'block/height/latest') == rollback_height
                results['phases']['legacy-upgrades-valid-before-h1'] = {'status': 'passed',
                    'checked_admission': checked, 'height': rollback_height,
                    'queue_discard': 'Offline restore of full pre-admission ledger and BFT snapshot while no quorum is online'}
                stop(0)
                for name in ['ledger-0', 'data-0']:
                    shutil.rmtree(run / name)
                    shutil.copytree(rollback / name, run / name)
                start(0)
                for i in [1, 2, 3]:
                    start(i)
                wait(lambda: min(heads()) >= proving_height, 'Proof-generation quorum restored')
                legacy_tx = json.loads(accepted_legacy.read_text())
                legacy = {'file': str(accepted_legacy), 'transaction_id': legacy_tx['id']}
                results['transactions']['legacy-v0-to-v1'] = legacy
                existing = [location(i, legacy) for i in range(4)]
                if any(height is not None for height in existing):
                    results['transactions']['legacy-v0-to-v1']['resumed_confirmation'] = existing
                elif cache:
                    response = post(0, pathlib.Path(legacy['file']))
                    results['transactions']['legacy-v0-to-v1']['admission'] = response
                    results['transactions']['legacy-v0-to-v1']['admission_may_have_raced_confirmation'] = validation_rejection(response)
                    save()
                    assert 200 <= response['status'] < 300 or validation_rejection(response), response
                else:
                    submit('legacy-v0-to-v1', legacy)
                legacy_height = included('legacy-v0-to-v1', legacy, maximum=args.h1)
                v1_sources = command('sources-v1', ['sources', '--dev-key', '0', '--height', str(legacy_height),
                    '--output-dir', str(out / 'sources-v1'), '--endpoint', 'http://127.0.0.1:3030'])['sources']
                assert v1_sources and all(source['version'] == '1u8' for source in v1_sources), v1_sources
                rotation_source = max(v1_sources, key=lambda source: source_amount(source['path']))['path']
                upgraded_pools = [pool(i) for i in range(4)]
                expected_upgraded_pool = results['historical_pool_before'][0] + source_amount(sources[3]['path'])
                assert upgraded_pools == [expected_upgraded_pool] * 4, upgraded_pools
                results['phases']['legacy-upgrade-pool'] = {'status': 'passed', 'pools': upgraded_pools,
                    'expected': expected_upgraded_pool, 'source_amount': source_amount(sources[3]['path'])}
                dormant_source = sources[2]['path']
                results['historical_v1_sources'] = v1_sources
            results['prepared_source_microcredits'] = sum(source_amount(path) for path in [sources[0]['path'], sources[1]['path'], rotation_source])
            rotating = build('prepare-rotation', 'prepare', 'falcon', source=rotation_source, start_height=args.h1, state=before)
            dormant = build('prepare-dormant', 'prepare', 'ml', source=dormant_source, start_height=args.h1, state=before)
            if not args.skip_legacy_proof:
                spent_preparation = build('prepare-spent-legacy-source', 'prepare', 'ml',
                    source=sources[3]['path'], start_height=args.h1, state=before)
            if args.skip_legacy_proof:
                rollback_height, rollback_hash, rollback = capture_rollback()
                start(0)
                wait(lambda: get(0, 'block/height/latest') >= rollback_height, 'Pre-activation snapshot validator restarted')
            assert max(heads()) < args.h1 - 1, 'Initial proof construction exceeded preparation boundary; increase --h1'
            reject('preparation-before-h1', pathlib.Path(preparation['file']))
            wait(lambda: min(heads()) >= args.h1 - 1, 'Candidate height reaches H1')
            # Both authorizations are valid in isolation; only one consumption may finalize.
            candidates = [('prepare-ml', preparation, 0), ('prepare-competing', competing, 1)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda candidate: post(candidate[2], pathlib.Path(candidate[1]['file'])), candidates))
            for (name, _, _), response in zip(candidates, responses):
                results['transactions'][name]['admission'] = response
            save()
            assert any(200 <= response['status'] < 300 for response in responses), responses
            assert all(200 <= response['status'] < 300 or validation_rejection(response) for response in responses), responses
            winners = wait(lambda: (found if (found := [tx for tx in [preparation, competing] if location(0, tx) is not None]) else None), 'Competing preparation winner')
            assert len(winners) == 1
            winner = winners[0]
            winner_name = 'prepare-ml' if winner is preparation else 'prepare-competing'
            included(winner_name, winner, minimum=args.h1, maximum=args.h2)
            for name, tx in [('prepare-falcon', falcon), ('prepare-rotation', rotating)]:
                submit(name, tx)
                included(name, tx, minimum=args.h1, maximum=args.h2)
            competing_locations = [[location(i, tx) for tx in [preparation, competing]] for i in range(4)]
            assert all(sum(height is not None for height in locations) == 1 for locations in competing_locations)
            assert len({tuple(locations) for locations in competing_locations}) == 1
            results['phases']['competing-preparation'] = {'status': 'passed', 'locations': competing_locations}
            reject('repeated-preparation', pathlib.Path(winner['file']))
            if not args.skip_legacy_proof:
                reject('preparation-of-legacy-spent-source', pathlib.Path(spent_preparation['file']))
                reject('competing-legacy-upgrade-after-h1', legacy_path)
                reject('unspent-legacy-upgrade-after-h1', dormant_legacy_path)
            for mutation in ['effects', 'signature', 'witness', 'network', 'key', 'replay']:
                path = out / f'invalid-{mutation}.json'
                options = ['test-mutation', '--transaction', dormant['file'], '--store', str(path), '--mutation', mutation]
                if mutation == 'key':
                    options += ['--key-file', str(out / 'attacker.key.json')]
                if mutation == 'replay':
                    options += ['--replay', winner['replay']]
                command(f'mutation-{mutation}', options)
                reject(f'changed-{mutation}', path)
            rotate = build('rotate-falcon-to-ml', 'rotate', 'falcon', input_id=rotating['output'], recipient='rotated')
            submit('rotate-falcon-to-ml', rotate)
            included('rotate-falcon-to-ml', rotate, minimum=args.h1, maximum=args.h2)
            charged_sources = [0, 1, 3] if not args.skip_legacy_proof else [0, 1, 2]
            historical_charge = sum(source_amount(sources[i]['path']) for i in charged_sources)
            results['expected_historical_pool'] = results['historical_pool_before'][0] + historical_charge
            stage1_root = same_state('stage1')
            stage1 = snapshot('before-h2')
            migration = build('migrate-ml', 'migrate', 'ml', input_id=winner['output'], start_height=args.h2, state=stage1)
            competing_migration = build('migrate-competing', 'migrate', 'ml', input_id=winner['output'], start_height=args.h2, state=stage1)
            migration_falcon = build('migrate-falcon', 'migrate', 'falcon', input_id=falcon['output'], start_height=args.h2, state=stage1)
            migration_rotated = build('migrate-rotated', 'migrate', 'rotated', input_id=rotate['output'], start_height=args.h2, state=stage1)
            assert max(heads()) < args.h2 - 1, 'Preparation tests exceeded recovery boundary; increase --h2'
            reject('migration-before-h2', pathlib.Path(migration['file']))
            wait(lambda: min(heads()) >= args.h2 - 1, 'Candidate height reaches H2')
            for name, tx in [('migrate-ml', migration), ('migrate-falcon', migration_falcon), ('migrate-rotated', migration_rotated)]:
                submit(name, tx)
                included(name, tx, minimum=args.h2)
            reject('dormant-legacy-after-h2', pathlib.Path(dormant['file']))
            reject('repeated-migration', pathlib.Path(migration['file']))
            reject('distinct-competing-migration', pathlib.Path(competing_migration['file']))
            if not args.skip_legacy_proof:
                reject('competing-legacy-upgrade-after-h2', legacy_path)
                reject('unspent-legacy-upgrade-after-h2', dormant_legacy_path)
            spend = build('spend-ml', 'spend', 'ml', input_id=migration['output'])
            competing_spend = build('spend-competing', 'spend', 'ml', input_id=migration['output'])
            spend_falcon = build('spend-falcon', 'spend', 'falcon', input_id=migration_falcon['output'])
            attacker_spend = out / 'attacker-authorization.json'
            command('attacker-authorization', ['test-mutation', '--transaction', spend['file'],
                    '--store', str(attacker_spend), '--mutation', 'authorization',
                    '--key-file', str(out / 'attacker.key.json'), '--state-file', str(snapshot('attacker-state'))])
            reject('disclosed-opening-with-attacker-pq-key', attacker_spend)
            for name, tx in [('spend-ml', spend), ('spend-falcon', spend_falcon)]:
                submit(name, tx)
                included(name, tx, minimum=args.h2)
            reject('double-spend-old-preparation', pathlib.Path(winner['file']))
            reject('double-spend-pq', pathlib.Path(spend['file']))
            reject('distinct-competing-pq-spend', pathlib.Path(competing_spend['file']))
            stage2_root = same_state('stage2')
            assert stage2_root != stage1_root
            results['phases']['activations'] = {'status': 'passed', 'boundary_hashes': {str(height): [get(i, f'block/{height}')['block_hash'] for i in range(4)]
                for height in [args.h1-1, args.h1, args.h1+1, args.h2-1, args.h2, args.h2+1]}}
            assert all(len(set(hashes)) == 1 for hashes in results['phases']['activations']['boundary_hashes'].values())
            save()

            checkpoint = min(heads())
            checkpoint_hash = get(0, f'block/{checkpoint}')['block_hash']
            stop_all()
            for i in range(4):
                start(i)
            wait(lambda: min(heads()) >= checkpoint, 'All-four persisted restart')
            assert same_state('persisted-restart') == stage2_root
            assert all(get(i, f'block/{checkpoint}')['block_hash'] == checkpoint_hash for i in range(4))

            # Fail one capable validator after native finalization and before the shared DB write.
            stop(0)
            fault_height = max(heads([1, 2, 3])) + 8
            start(0, fault_height=fault_height)
            wait(lambda: get(0, 'block/height/latest') >= fault_height - 2, 'Fault validator synchronized')
            crash_tx = build('interrupted-spend', 'spend', 'ml', input_id=spend['output'], start_height=fault_height)
            wait(lambda: min(heads()) >= fault_height - 1, 'Candidate crash height')
            submit('interrupted-spend', crash_tx)
            wait(lambda: processes[0].poll() == 86, 'Validator interrupted before shared commit', allow_exit=[0])
            fault_logs = [path for path in run.glob('node-0-*.log') if 'RECOVERY_TEST_EXIT_BEFORE_SHARED_COMMIT' in path.read_text()]
            assert fault_logs, 'No deterministic finalization fault marker'
            fault_marker = re.search(r'RECOVERY_TEST_EXIT_BEFORE_SHARED_COMMIT prior_height=(\d+) candidate_height=(\d+) recovery_ids=(\S+)', fault_logs[-1].read_text())
            assert fault_marker is not None
            prior_height, candidate_height = map(int, fault_marker.group(1, 2))
            assert candidate_height >= fault_height and prior_height + 1 == candidate_height
            assert crash_tx['transaction_id'] in fault_marker.group(3).split(',')
            results['phases']['interrupted-finalization'] = {'status': 'passed', 'fault_height': fault_height,
                'prior_height': prior_height, 'candidate_height': candidate_height,
                'exit_status': 86, 'fault_logs': [str(path) for path in fault_logs]}
            stop(0)
            start(0)
            included('interrupted-spend', crash_tx, minimum=args.h2)
            crash_root = same_state('crash-recovered')

            stop_all()
            groups = [[0, 1], [0, 1], [2, 3], [2, 3]]
            for i in range(4):
                start(i, groups[i])
            wait(lambda: len(heads()) == 4, 'Partition nodes ready')
            observed = wait(lambda: topology(groups), 'Observed connected 2+2 topology')
            settling_heads = heads()
            before_heads = stable_heads()
            before_roots = [get(i, 'recovery/state')['root'] for i in range(4)]
            time.sleep(12)
            after_heads = heads()
            after_topology = topology(groups)
            assert before_heads == after_heads and after_topology is not None
            assert [get(i, 'recovery/state')['root'] for i in range(4)] == before_roots == [crash_root] * 4
            results['phases']['partition'] = {'status': 'passed', 'observed_bft_peers': observed,
                'confirmed_bft_peers': after_topology, 'settling_heads': settling_heads,
                'measurement_seconds': 12, 'heads_before': before_heads, 'heads_after': after_heads}
            save()
            stop_all()
            for i in range(4):
                start(i)
            wait(lambda: topology([list(range(4))] * 4), 'Full topology reconnected')
            wait(lambda: min(heads()) > max(after_heads) + 2, 'Consensus resumed')
            resumed = build('reconnected-spend', 'spend', 'ml', input_id=crash_tx['output'])
            submit('reconnected-spend', resumed)
            included('reconnected-spend', resumed, minimum=args.h2)
            reconnected_root = same_state('reconnected-transactions')
            stop(0)
            for name in ['ledger-0', 'data-0']:
                shutil.move(run / name, out / f'pre-rollback-{name}')
                shutil.copytree(rollback / name, run / name)
            start(0)
            wait(lambda: get(0, 'block/height/latest') > checkpoint, 'Restored validator replays both activation histories')
            assert same_state('rollback-replayed') == reconnected_root
            assert get(0, f'block/{rollback_height}')['block_hash'] == rollback_hash
            assert get(0, f'block/{checkpoint}')['block_hash'] == checkpoint_hash
            assert location(0, winner) == results['transactions'][winner_name]['accepted_height']
            assert location(0, migration) == results['transactions']['migrate-ml']['accepted_height']
            assert location(0, resumed) == results['transactions']['reconnected-spend']['accepted_height']
            results['phases']['rollback-replayed']['method'] = 'Offline full-state restore then synchronization with unchanged finalized chain'
            results['phases']['accepted-effects-conservation'] = {'status': 'passed', **accepted_accounting(),
                'method': 'Finalized client effects and disclosed source amounts match independently decoded actual ledger checkpoints'}
            results['core_status'] = 'passed'
            save()

        if args.client_relay:
            results['client_relay_status'] = 'running'
            stop(0)
            start(0, p2p_peers='127.0.0.1:4134')
            start(4, client=True, p2p_peers='127.0.0.1:4130,127.0.0.1:4135')
            start(5, client=True, p2p_peers='127.0.0.1:4134')
            wait(lambda: len(heads(range(6))) == 6, 'Four validators and two clients ready')

            def relay_topology():
                expected = {0: {'127.0.0.1:4134'}, 4: {'127.0.0.1:4130', '127.0.0.1:4135'},
                            5: {'127.0.0.1:4134'}}
                observed = {i: get(i, 'connections/p2p/all') for i in expected}
                return observed if all(set(observed[i]) == peers for i, peers in expected.items()) else None

            observed = wait(relay_topology, 'Observed client-origin to client-relay to validator topology')
            synchronization_height = min(heads())
            synchronization_hash = get(0, f'block/{synchronization_height}')['block_hash']
            wait(lambda: min(heads(range(6))) >= synchronization_height, 'Both clients replayed the finalized chain')
            assert all(get(i, f'block/{synchronization_height}')['block_hash'] == synchronization_hash for i in range(6))
            assert all(get(i, 'recovery/state')['root'] == reconnected_root for i in range(6))
            relay = build('client-relayed-spend', 'spend', 'ml', input_id=resumed['output'])
            assert all(location(i, relay) is None for i in range(6))
            assert relay_topology() == observed
            submit('client-relayed-spend', relay, node=5)
            included('client-relayed-spend', relay, minimum=args.h2, indices=range(6))
            same_state('six-node-client-relay', indices=range(6))
            results['phases']['client-p2p-relay'] = {'status': 'passed', 'observed_p2p_peers': observed,
                'confirmed_p2p_peers': relay_topology(), 'synchronization_height': synchronization_height,
                'synchronization_hash': synchronization_hash, 'rest_submission_node': 5,
                'path': 'Client 5 REST -> client 4 receive/verification queue -> validator 0 -> four-validator BFT',
                'transaction_id': relay['transaction_id'], 'accepted_height': relay['accepted_height']}
            assert results['phases']['client-p2p-relay']['confirmed_p2p_peers'] == observed
            results['client_relay_status'] = 'passed'
        results['status'] = 'passed'
    except KeyboardInterrupt:
        results['status'] = 'interrupted'
        results['error'] = 'KeyboardInterrupt: validation did not complete'
        save()
        raise
    except Exception as error:
        results['status'] = 'failed'
        if results.get('client_relay_status') == 'running':
            results['client_relay_status'] = 'failed'
        results['error'] = f'{type(error).__name__}: {error}'
        save()
        raise
    finally:
        stop_all()
        save()
    print(json.dumps({'status': results['status'], 'evidence': str(out)}, indent=2), flush=True)


if __name__ == '__main__':
    main()
