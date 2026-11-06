#!/usr/bin/env python3
"""Decode public recovery state independently of the Rust implementation.

This validates canonical encoding, state identities and the reported digest.
It does not verify ownership, signatures, consensus or checkpoint authenticity.
"""

import argparse
import hashlib
import json
import pathlib
import string
import struct


MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_REPLAYS = 100_000
PROFILE_KEY_BYTES = {(1, 1): 1952, (2, 1): 897}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(domain, *parts):
    digest = hashlib.sha3_512()
    for part in (domain, *parts):
        digest.update(struct.pack('<Q', len(part)))
        digest.update(part)
    return digest.digest()


def _hex(value, name, *, size=None, maximum=None):
    _require(isinstance(value, str), f'{name} must be hexadecimal text')
    _require(len(value) % 2 == 0, f'{name} has an odd hexadecimal length')
    if size is not None:
        _require(len(value) == size * 2, f'{name} must contain {size} bytes')
    if maximum is not None:
        _require(len(value) <= maximum * 2, f'{name} exceeds its byte limit')
    _require(all(character in string.hexdigits for character in value), f'{name} contains invalid hexadecimal')
    return bytes.fromhex(value)


class _Reader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def take(self, length):
        end = self.position + length
        _require(end <= len(self.data), 'Truncated recovery checkpoint')
        result = self.data[self.position:end]
        self.position = end
        return result

    def uint(self, size):
        return int.from_bytes(self.take(size), 'little')

    def count(self, maximum):
        count = self.uint(4)
        _require(count <= maximum, 'Recovery checkpoint count exceeds its limit')
        return count

    def blob(self, maximum):
        return self.take(self.count(maximum))


def _policy(reader):
    activations = []
    for _ in range(2):
        tag = reader.uint(1)
        _require(tag in (0, 1), 'Invalid activation option tag')
        activations.append(reader.uint(4) if tag else None)
    preparation, recovery = activations
    _require(recovery is None or preparation is not None, 'Recovery requires preparation')
    _require(recovery is None or preparation < recovery, 'Activation heights must increase')
    profiles = [(reader.uint(2), reader.uint(2)) for _ in range(reader.count(2))]
    _require(profiles and profiles == sorted(set(profiles)), 'Profiles must be sorted, unique and nonempty')
    _require(all(profile in PROFILE_KEY_BYTES for profile in profiles), 'Unsupported signature profile')
    aggregates = [reader.uint(2) for _ in range(reader.count(1))]
    _require(aggregates in ([], [1]), 'Unsupported aggregate policy')
    _require(not aggregates or (2, 1) in profiles, 'Falcon aggregation requires the Falcon profile')
    return {'preparation': preparation, 'recovery': recovery,
            'profiles': [list(profile) for profile in profiles], 'aggregates': aggregates}


def _matching_rpc_policy(value, expected):
    _require(isinstance(value, dict), 'RPC policy must be an object')
    _require(set(value) == set(expected), 'RPC policy has missing or unsupported fields')
    for name in ('preparation', 'recovery'):
        height = value[name]
        _require(height is None or type(height) is int and 0 <= height <= 0xffff_ffff,
                 f'Invalid RPC {name} height')
    _require(isinstance(value['profiles'], list), 'RPC profiles must be an array')
    for profile in value['profiles']:
        _require(isinstance(profile, list) and len(profile) == 2
                 and all(type(identifier) is int for identifier in profile), 'Invalid RPC profile')
    _require(isinstance(value['aggregates'], list)
             and all(type(identifier) is int for identifier in value['aggregates']), 'Invalid RPC aggregates')
    _require(value == expected, 'RPC policy differs from the encoded checkpoint policy')


def _record(reader, network, profiles):
    start = reader.position
    version, owner_tag = reader.uint(1), reader.uint(1)
    _require(version in range(4), 'Unsupported record version')
    if owner_tag == 0:
        _require(version in (0, 1), 'Enrolled record cannot have a legacy owner')
        address = reader.blob(32)
        _require(len(address) == 32, 'Legacy owner must contain 32 bytes')
        owner = {'kind': 'legacy', 'address_hex': address.hex()}
    elif owner_tag == 1:
        _require(version in (2, 3), 'Historical record cannot have an enrolled owner')
        algorithm, parameters = reader.uint(2), reader.uint(2)
        profile = (algorithm, parameters)
        _require(profile in PROFILE_KEY_BYTES and list(profile) in profiles, 'Record profile is not enabled')
        public_key = reader.blob(1952)
        _require(len(public_key) == PROFILE_KEY_BYTES[profile], 'Incorrect public key length')
        identity = _digest(b'AleoRecoveryExperimentalKey1', network,
                           struct.pack('<H', algorithm), struct.pack('<H', parameters), public_key)
        owner = {'kind': 'enrolled', 'algorithm': algorithm, 'parameters': parameters,
                 'public_key': public_key.hex(), 'key_identity': identity.hex()}
    else:
        raise ValueError('Unsupported owner tag')
    amount = reader.uint(8)
    nonce, opening = reader.take(64), reader.take(64)
    identity = _digest(b'AleoRecoveryExperimentalRecord1', network, reader.data[start:reader.position])
    return identity, {'version': version, 'owner': owner, 'amount': amount,
                      'nonce': nonce.hex(), 'opening': opening.hex()}


def _ordered_ids(reader, maximum, name):
    identities = [reader.take(64) for _ in range(reader.count(maximum))]
    _require(all(previous < current for previous, current in zip(identities, identities[1:])),
             f'{name} must be sorted and unique')
    return identities


def decode_checkpoint(state: dict) -> dict:
    """Decode one RPC response, without trusting its root or numeric summaries.

    Public keys are checked for profile and length only. This decoder makes no
    cryptographic key-validity or authenticated-state claim.
    """
    _require(isinstance(state, dict), 'Recovery RPC state must be an object')
    _require(all(field in state for field in ('network', 'root', 'checkpoint', 'policy')),
             'Recovery RPC state has missing fields')
    network = _hex(state['network'], 'Network', size=64)
    root = _hex(state['root'], 'Root', size=64)
    checkpoint = _hex(state['checkpoint'], 'Checkpoint', maximum=MAX_CHECKPOINT_BYTES)
    _require(_digest(b'AleoRecoveryStateRoot1', checkpoint) == root, 'Recovery state root mismatch')
    reader = _Reader(checkpoint)
    _require(reader.take(len(b'AleoRecoveryState1')) == b'AleoRecoveryState1', 'Unsupported checkpoint domain')
    _require(reader.take(64) == network, 'Recovery network mismatch')
    policy_start = reader.position
    policy = _policy(reader)
    policy_bytes = checkpoint[policy_start:reader.position]
    _matching_rpc_policy(state['policy'], policy)
    fees = reader.uint(16)
    records = {}
    previous = None
    for _ in range(reader.count(MAX_RECORDS)):
        identity, record = _record(reader, network, policy['profiles'])
        _require(previous is None or previous < identity, 'Records must be sorted by identity and unique')
        previous = identity
        records[identity.hex()] = record
    spent = _ordered_ids(reader, MAX_RECORDS, 'Spent identities')
    _require(all(identity.hex() in records for identity in spent), 'Spent identity has no record')
    replay = _ordered_ids(reader, MAX_REPLAYS, 'Replay identities')
    _require(reader.position == len(checkpoint), 'Trailing recovery checkpoint data')
    spent_ids = [identity.hex() for identity in spent]
    spent_set = set(spent_ids)
    unspent_outputs = {identity: record['amount'] for identity, record in records.items() if identity not in spent_set}
    result = {'network': network.hex(), 'policy': policy, 'root': root.hex(),
              'policy_commitment': _digest(b'AleoRecoveryExperimentalPolicy1', network, policy_bytes).hex(),
              'checkpoint_bytes': len(checkpoint), 'records': records, 'spent_ids': spent_ids,
              'replay_ids': [identity.hex() for identity in replay], 'unspent_outputs': unspent_outputs,
              'unspent_total': sum(unspent_outputs.values()), 'fees': fees}
    if 'height' in state:
        _require(type(state['height']) is int and 0 <= state['height'] <= 0xffff_ffff, 'Invalid RPC height')
        result['height'] = state['height']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state', type=pathlib.Path, help='Retained recovery/state JSON response')
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    _require(args.state.stat().st_size <= MAX_CHECKPOINT_BYTES * 2 + 65_536, 'RPC state JSON exceeds limit')
    result = decode_checkpoint(json.loads(args.state.read_text()))
    encoded = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
