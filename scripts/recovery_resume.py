#!/usr/bin/env python3
"""Retain and verify offline, node-specific local recovery test inputs."""

import argparse
import hashlib
import json
import pathlib
import shutil


PROOFS = {'legacy-competing-upgrade': 0, 'legacy-dormant-upgrade': 2, 'legacy-v0-to-v1': 3}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_inventory(directory):
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink in retained input: {path}')
        if path.is_file():
            files[str(path.relative_to(directory))] = digest(path)
    if not files:
        raise ValueError(f'Empty retained input: {directory}')
    return files


def validate_upgrade(transaction, serial):
    assert transaction['type'] == 'execute'
    transitions = transaction['execution']['transitions']
    assert len(transitions) == 1
    transition = transitions[0]
    assert transition['program'] == 'credits.aleo' and transition['function'] == 'upgrade'
    assert len(transition['inputs']) == 1
    assert transition['inputs'][0]['type'] == 'record'
    assert transition['inputs'][0]['id'] == serial, 'Cached proof consumes a different historical source'


def create_cache(run):
    """All validators must be stopped before this offline snapshot is created."""
    run = run.resolve()
    manifest_path = run / 'legacy-cache.json'
    if manifest_path.exists():
        raise ValueError(f'Cache already exists: {manifest_path}')
    results = json.loads((run / 'results.json').read_text())
    checkpoint = results['phases']['rollback-checkpoint']
    assert checkpoint['minimum_height'] < checkpoint['before_activation']
    sources = results['historical_sources']
    fixture_path = pathlib.Path(results['genesis_file']).parent / 'manifest.json'
    fixture = json.loads(fixture_path.read_text())
    assert fixture['genesis_hash'] == results['genesis_hash']
    assert fixture['files']['genesis.bin']['sha256'] == results['genesis_sha256']
    shutil.copyfile(fixture_path, run / 'legacy-cache-fixture.json')
    proofs = {}
    for name, source_index in PROOFS.items():
        phase = results['phases'][f'proof-{name}']
        assert phase['status'] == 'passed'
        path = run / f'{name}.json'
        transaction = json.loads(path.read_text())
        fixture_source = next(record for record in fixture['records']
                              if record['commitment'] == sources[source_index]['commitment'])
        validate_upgrade(transaction, fixture_source['serial'])
        source_path = pathlib.Path(sources[source_index]['path'])
        assert source_path.resolve().is_relative_to(run)
        source = json.loads(source_path.read_text())
        commands = [command for command in results['commands']
                    if command.get('returncode') == 0 and str(path) in command['command']]
        assert commands and source['record'] in commands[-1]['command']
        proofs[name] = {'file': path.name, 'sha256': digest(path), 'transaction_id': transaction['id'],
                        'source_index': source_index, 'source_commitment': sources[source_index]['commitment'],
                        'source_serial': fixture_source['serial'], 'producer_command': commands[-1],
                        'source_file': str(source_path.relative_to(run)), 'source_sha256': digest(source_path),
                        'original_generation_seconds': phase['seconds']}
    snapshot = run / 'legacy-resume-state'
    snapshot.mkdir()
    original_files = {}
    for i in range(4):
        for kind in ['ledger', 'data']:
            name = f'{kind}-{i}'
            original_files.update({f'{name}/{path}': value
                                   for path, value in file_inventory(run / 'nodes' / name).items()})
            shutil.copytree(run / 'nodes' / name, snapshot / name)
    after_files = {}
    for i in range(4):
        for kind in ['ledger', 'data']:
            name = f'{kind}-{i}'
            after_files.update({f'{name}/{path}': value
                                for path, value in file_inventory(run / 'nodes' / name).items()})
    assert original_files == after_files == file_inventory(snapshot), 'Node files changed during offline capture'
    identity = {'format': 1, 'network': results['network'], 'schedule': results['schedule'],
                'cache_tool_sha256': digest(pathlib.Path(__file__)),
                'genesis_hash': results['genesis_hash'], 'genesis_sha256': results['genesis_sha256'],
                'original_binary_sha256': results['binary_sha256'],
                'original_results_sha256': digest(run / 'results.json'),
                'original_harness_sha256': results['harness_sha256'],
                'historical_pool_before': results['historical_pool_before'],
                'fixture_file': 'legacy-cache-fixture.json', 'fixture_sha256': digest(run / 'legacy-cache-fixture.json'),
                'anchor': checkpoint, 'proofs': proofs, 'state_directory': snapshot.name,
                'state_files': file_inventory(snapshot),
                'limits': 'Cached proofs are untrusted inputs; the resumed validators must verify them again. '
                          'Each validator retains its own ledger and BFT cache. No prior run pass is implied.'}
    manifest_path.write_text(json.dumps(identity, indent=2) + '\n')
    return manifest_path


def read_cache(run):
    run = run.resolve()
    manifest = json.loads((run / 'legacy-cache.json').read_text())
    assert manifest['format'] == 1
    if manifest['proofs']:
        assert set(manifest['proofs']) == set(PROOFS)
        assert digest(run / manifest['fixture_file']) == manifest['fixture_sha256']
    directory = run / manifest['state_directory']
    assert directory.resolve().is_relative_to(run)
    assert file_inventory(directory) == manifest['state_files'], 'Retained node state changed'
    for proof in manifest['proofs'].values():
        for file_key, hash_key in [('file', 'sha256'), ('source_file', 'source_sha256')]:
            path = run / proof[file_key]
            assert path.resolve().is_relative_to(run)
            assert digest(path) == proof[hash_key], f'Retained artifact changed: {path}'
        transaction = json.loads((run / proof['file']).read_text())
        assert transaction['id'] == proof['transaction_id']
        validate_upgrade(transaction, proof['source_serial'])
    return manifest


def restore_cache(run, destination):
    manifest = read_cache(run)
    directory = run / manifest['state_directory']
    for i in range(4):
        for kind in ['ledger', 'data']:
            name = f'{kind}-{i}'
            shutil.copytree(directory / name, destination / name)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'verify'])
    parser.add_argument('run', type=pathlib.Path)
    args = parser.parse_args()
    if args.action == 'create':
        print(create_cache(args.run))
    else:
        identity = read_cache(args.run)
        print(json.dumps({'status': 'verified', 'proofs': list(identity['proofs']),
                          'state_files': len(identity['state_files'])}, indent=2))
