"""Local four-validator evaluation. Uses disposable data, loopback, and test keys."""
import json
import os
import pathlib
import signal
import subprocess
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(tempfile.mkdtemp(prefix='snarkos-recovery-e2e-'))
BINARY = ROOT / 'target/debug/snarkos'
EXPECTED = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
SCHEDULE = ','.join(map(str, [*range(22), 30, 40]))
version = subprocess.check_output([str(BINARY), '--version'], text=True)
if EXPECTED not in version:
    raise RuntimeError('Rebuild this checkout with --features test_network and refreshed Git metadata')
print(f'E2E output: {OUT}', flush=True)
RUN = pathlib.Path(tempfile.mkdtemp(prefix='devnet-', dir=OUT))
http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
processes = {}
streams = {}
results = {'binary': version.strip(), 'run_directory': str(RUN), 'schedule': SCHEDULE,
           'limits': ['Testnet ID 1, local test keys only',
                      'Full topology uses local dev discovery; split groups use trusted-only connections',
                      'Topology partition includes process restart; it is not packet filtering',
                      'No recovery transaction or post-quantum proof backend exists',
                      'No forced finalized-chain reorganization is tested'], 'phases': {}}


def save():
    (OUT / 'devnet-results.json').write_text(json.dumps(results, indent=2) + '\n')


def get(i, path):
    with http.open(f'http://127.0.0.1:{3030+i}/v2/testnet/{path}', timeout=3) as response:
        return json.load(response)


def heads():
    return [get(i, 'block/height/latest') for i in range(4)]


def wait_for(predicate, description, timeout=600):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        for i, process in processes.items():
            if process.poll() is not None:
                raise RuntimeError(f'Node {i} exited with {process.returncode}; inspect {RUN}')
        try:
            value = predicate()
            if value:
                print(description, value, flush=True)
                return value
        except (OSError, ValueError) as error:
            last_error = str(error)
        time.sleep(1)
    raise TimeoutError(f'{description}: {last_error}')


def start(i, group=None):
    peers = list(range(4)) if group is None else list(group)
    validators = ','.join(f'127.0.0.1:{5000+j}' for j in peers if j != i)
    env = dict(os.environ)
    env.pop('TEST_BOOTSTRAP_PEERS', None)
    env.update(CONSENSUS_VERSION_HEIGHTS=SCHEDULE, RAYON_NUM_THREADS='2', TOKIO_WORKER_THREADS='2')
    log = RUN / f'node-{i}-{time.time_ns()}.log'
    stream = log.open('w')
    command = [str(BINARY), '--noupdater', 'start', '--validator', '--dev', str(i),
               '--network', '1', '--dev-num-validators', '4', '--nodisplay', '--nobanner',
               '--nocdn', '--no-dev-txs', '--peers', '',
               '--validators', validators, '--node', f'127.0.0.1:{4130+i}',
               '--bft', f'127.0.0.1:{5000+i}', '--rest', f'127.0.0.1:{3030+i}', '--nojwt',
               '--ledger-storage', str(RUN / f'ledger-{i}'),
               '--node-data-storage', str(RUN / f'data-{i}'), '--logfile', str(log) + '.internal']
    if group is not None:
        command.append('--trusted-peers-only')
    processes[i] = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    streams[i] = stream


def stop(i):
    process = processes.pop(i, None)
    if process and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            results.setdefault('forced_stops', []).append(i)
    stream = streams.pop(i, None)
    if stream:
        stream.close()


def stop_all():
    for i in list(processes):
        stop(i)


def shared_hash(height):
    blocks = [get(i, f'block/{height}') for i in range(4)]
    hashes = [block['block_hash'] for block in blocks]
    if len(set(hashes)) != 1:
        raise AssertionError(f'Validators disagree at height {height}: {hashes}')
    return hashes[0]


try:
    for i in range(4):
        start(i)
    wait_for(lambda: len(heads()) == 4, 'All local REST servers ready')
    versions = [get(i, 'version') for i in range(4)]
    assert all(v['git_commit'] == EXPECTED and v['consensus_heights'][-2:] == [30, 40] for v in versions)
    reached = wait_for(lambda: (h if min(h := heads()) >= 42 else None), 'All validators passed V24')
    active = [get(i, 'consensus_version') for i in range(4)]
    assert active == [24] * 4
    boundary_hashes = {str(h): shared_hash(h) for h in [29, 30, 31, 39, 40, 41]}
    results['phases']['activation'] = {'status': 'passed', 'heads': reached,
        'active_versions': active, 'shared_boundary_block_hashes': boundary_hashes,
        'scope': 'Agreement and empty-block consensus only; not recovery spending'}
    save()

    checkpoint = min(heads())
    checkpoint_hash = shared_hash(checkpoint)
    stop(0)
    target = max(get(i, 'block/height/latest') for i in [1, 2, 3]) + 3
    wait_for(lambda: all(get(i, 'block/height/latest') >= target for i in [1, 2, 3]), 'Three validators continued')
    start(0)
    rejoined = wait_for(lambda: (h if min(h := heads()) >= target else None), 'Restarted validator caught up')
    assert shared_hash(checkpoint) == checkpoint_hash
    results['phases']['restart'] = {'status': 'passed', 'heads': rejoined,
        'checkpoint_height': checkpoint, 'checkpoint_hash': checkpoint_hash}
    save()

    stop_all()
    for i in range(4):
        start(i, [0, 1] if i < 2 else [2, 3])
    wait_for(lambda: len(heads()) == 4, 'Partition topology REST ready')
    time.sleep(5)
    connections = [get(i, 'connections/bft/count') for i in range(4)]
    before = heads()
    time.sleep(12)
    after = heads()
    isolated = all(count <= 1 for count in connections)
    stalled = before == after
    results['phases']['partition'] = {'status': 'passed' if isolated and stalled else 'failed',
        'bft_connections': connections, 'heads_before': before, 'heads_after': after,
        'connected_two_by_two': connections == [1] * 4,
        'scope': 'Configured pairs; zero connections demonstrate full isolation, not a connected 2+2 split'}
    save()

    stop_all()
    for i in range(4):
        start(i)
    target = max(after) + 3
    resumed = wait_for(lambda: (h if min(h := heads()) >= target else None), 'Full topology resumed')
    assert shared_hash(checkpoint) == checkpoint_hash
    results['phases']['rejoin'] = {'status': 'passed', 'heads': resumed,
        'preserved_checkpoint_hash': checkpoint_hash}
    results['status'] = 'passed' if all(p['status'] == 'passed' for p in results['phases'].values()) else 'failed'
    save()
except Exception as error:
    results['status'] = 'failed'
    results['error'] = f'{type(error).__name__}: {error}'
    save()
    raise
finally:
    stop_all()
    save()
print(json.dumps(results, indent=2), flush=True)
if results['status'] != 'passed':
    raise SystemExit(1)
