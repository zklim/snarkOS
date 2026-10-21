# Quantum recoverability: preparation integration

## Scope and source

This branch integrates recovery **preparation** with snarkOS mainnet source. It does
not implement recovery transactions, a post-quantum proof verifier, or subsequent
spending under a new authorization scheme. No live network was deployed.

- Node base: `f2eea52d9f638ed30e8f9d9fb578799c3a24e2e5` (mainnet).
- VM: `c493bc514f0850d4e07a0135b0e4f845e38d0c73` on `zklim/snarkVM`'s
  `quantum-recoverability` branch, based on `13ec14ccf1b156caa05bf2d5154ac5a0aaa50a56`.
- Cargo.toml and Cargo.lock pin that VM commit; a sibling checkout is not required.
- Validation used Rust 1.96.1, nightly-2026-04-02 rustfmt, and four build jobs.

## Implementation and reasoning

The fork derives a seed-bound, domain/network-separated SHA3-512 credential and
V2 record opening. Native and circuit helpers preserve the old owner/data and
require a fresh nonce. Their explicit V2 codec remains separate from ordinary
transaction serialization. This is local witness preparation: it neither checks
ledger spentness nor spends, enrolls, or publishes a record.

The node adds message versions **V23=34** and **V24=35**. Published activation
heights remain unscheduled (`u32::MAX`), so ordinary network behavior is unchanged.
V23 permits preparation; V24 retires every existing transaction-authorization
path, including fees. There is no replacement spending path: scheduling V24
would stop existing transactions, even though empty blocks can continue.

A failing VM regression exposed an activation error: a cached legacy transaction
was accepted in candidate block 40 while admission still used head 39's rules.
The VM fix applies the **candidate height** during preparation and speculation,
rejects legacy accepted transactions and rejected-transaction fees during candidate
verification, and gates finalization before persistent updates. Pre-V24 rules
remain available for historical verification.

## Recorded validation

| Check | Result |
| --- | --- |
| Node build, strict workspace/all-target/all-feature Clippy; both formatters | Passed |
| Message policy | 3 tests passed; V23/V24 boundaries checked on all three network types |
| Actual VM retirement | Failed before fix: 1 legacy transfer accepted at height 40. Passed after fix: acceptance at 39; rejection at 40/41, including cache, fee-only and direct-finalization paths |
| Ledger-backed preparation | Genesis record commitment and absent spent serial checked; original-seed decryption and 512-bit V2 opening passed; source remained unspent |
| Native / circuit preparation | 10 native tests, 5 circuit tests; 64 generated migration cases |
| Bounded mutation fuzzing | 4,096 cases: 353 canonical decodes, 3,743 rejected; no panic; not coverage-guided fuzzing |
| Other focused checks | 7 archive, 3 seed-derivation, 2 process-policy, and 4 independent Python framing/hash vectors passed |
| Historical proof | Generated V10-version execution verified historically and rejected under explicit V24; not an on-chain historical-asset spend |
| Four validators | Same block hashes at 29/30/31 and 39/40/41; all V24 at 42; persisted restart caught up at 46; isolation stalled at 46; rejoin reached 49 with checkpoint 42 preserved |

The recorded E2E run used a local source override containing the same VM changes;
publication replaces that override with the committed Git dependency. That source
selection is checked separately; the full node runtime was not rerun solely for
the path-to-Git dependency change.

Important limits: the configured pair split produced **zero** BFT connections per
node, demonstrating full isolation rather than a connected 2+2 partition. An
all-trusted-only run stalled during a validator outage: discovery requests led to
responses that trusted-only mode rejected, disconnecting peers. No BFT source fix
is included. The successful run uses normal local discovery outside isolation.
No prohibited transaction was injected into the empty-block devnet; real-transfer
enforcement is established by the VM regression. No forced finalized reorg was
tested. A broad unrelated message suite was interrupted during expensive proof
fixtures; it is not claimed as passing. Raw cloud-run logs remain under
`/workspace/security-evaluation/`.

## Reproduce

With Rust/native build prerequisites and verified public parameter caches ready,
run from this checkout (the E2E harness requires Python 3.9+ and free loopback
ports 3030–3033, 4130–4133 and 5000–5003):

```bash
# Refresh embedded Git metadata when reusing a target directory after a checkout.
touch build.rs node/rest/build.rs node/network/build.rs node/metrics/build.rs
cargo build --locked --bin snarkos --features test_network
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo +nightly-2026-04-02 fmt --all -- --check
cargo test --locked --profile dev --lib -p snarkos-node-router-messages --features snarkvm/test test_consensus_constants
cargo test --locked --profile dev --lib -p snarkos-node-router-messages --features snarkvm/test test_latest_message_version_has_scheduled_height
CONSENSUS_VERSION_HEIGHTS=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,30,40 cargo test --locked --profile dev --lib -p snarkos-node-router-messages --features snarkvm/test test_recovery_message_activation_boundaries -- --ignored --nocapture
python3 scripts/test_quantum_recoverability.py
```

The harness prints its temporary output directory, retains JSON/results and node
logs, and stops only its own processes. It uses testnet ID 1, development keys,
loopback endpoints, empty blocks, no external bootstrappers, and an isolated
schedule: V1–V22 at 0–21, V23 at 30, V24 at 40.

In a separate VM checkout at the commit above, run the actual-transaction test:

```bash
CONSENSUS_VERSION_HEIGHTS=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,30,40 cargo test --locked --lib -p snarkvm-synthesizer --features test,dev-print test_recovery_retirement_candidate_block_boundary -- --ignored --nocapture
```

## Costs and remaining protocol

Measured sample circuits use about 7,916 migration constraints and
928,831–928,834 seed-plus-opening constraints. Sample one-entry V2 encodings are
90 private-owner or 87 public-owner bytes. These are circuit/encoding costs, not
production proof throughput measurements.

SHA3 hashing is a commitment construction, not an ownership proof system. The
seed must remain secret; an attacker possessing the same seed has the same
witness. Legacy signatures, commitments/serials, Varuna/KZG soundness and validator
authentication are not made post-quantum by this hash. The original [ZIP 2005](https://github.com/zcash/zips/blob/a34cfc091434275120ff017c92c3b50d3cef623a/zips/zip-2005.md) and [ZIP 229](https://github.com/zcash/zips/blob/a34cfc091434275120ff017c92c3b50d3cef623a/zips/zip-0229.md)
references distinguish preparation from a future recovery protocol; they do not
supply a complete verifier here.

Recovery spending still needs an authenticated pre-attack checkpoint and ownership
binding, a specified post-quantum proof relation/backend, supported program/asset
adapters, a network-bound transaction and fee envelope, recovery nullifiers,
atomic exclusion across old/new spend paths, and persistence/reorg rules.
Recovery replay, front-running, cross-network spending, spent/repeated recovery
and double spending remain unimplemented and untested. These guarantees require
a separate protocol implementation and independent cryptographic review.
