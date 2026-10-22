#!/usr/bin/env python3
"""Four isolated startup failures; this does not claim a working recovery devnet."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(tempfile.mkdtemp(prefix='recovery-backend-unavailable-'))
SCHEDULE = ','.join(map(str, list(range(22)) + [30, 40]))


def check(index):
    env = dict(os.environ, CONSENSUS_VERSION_HEIGHTS=SCHEDULE, RAYON_NUM_THREADS='1', TOKIO_WORKER_THREADS='1')
    env.pop('TEST_BOOTSTRAP_PEERS', None)
    command = [str(ROOT / 'target/debug/snarkos'), '--noupdater', 'start',
               '--validator', '--dev', str(index), '--network', '1',
               '--dev-num-validators', '4', '--nodisplay', '--nobanner',
               '--nocdn', '--no-dev-txs', '--peers', '', '--validators', '',
               '--node', f'127.0.0.1:{4130+index}', '--bft', f'127.0.0.1:{5000+index}',
               '--rest', f'127.0.0.1:{3030+index}', '--nojwt',
               '--ledger-storage', str(OUT / f'ledger-{index}'),
               '--node-data-storage', str(OUT / f'data-{index}'),
               '--logfile', str(OUT / f'internal-{index}.log')]
    result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    (OUT / f'node-{index}.log').write_text(output)
    assert result.returncode != 0, output
    assert 'Missing consensus-integrated recovery backend' in output, output
    assert not (OUT / f'ledger-{index}').exists(), 'Node created a ledger before checking capabilities'
    return {'node': index, 'exit': result.returncode, 'required_backend_failure': True}


if __name__ == '__main__':
    print(OUT, flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(check, range(4)))
    evidence = {'scope': 'four startup refusals; no blocks, transactions, partition or restart claims', 'nodes': results}
    (OUT / 'results.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))
