> Publication: see [RECOVERY_PUBLICATION.md](RECOVERY_PUBLICATION.md). The report below records the validated pre-publication snapshot.

# snarkOS: recovery changes versus mainnet

Compared on 2026-11-06 against **`upstream/mainnet` = `d30bf265c64ef9c6624d8b6859040be88030b901`**
([pinned baseline](https://github.com/aleo-labs-protocol/snarkOS/tree/d30bf265c64ef9c6624d8b6859040be88030b901)).
The baseline is an ancestor of the recovery work. This covers all earlier committed work,
current tracked edits and non-ignored new files, not just the latest diff against HEAD.

| Variant | HEAD (does not include dirty edits) | Commits above mainnet | Changed paths¹ |
| --- | --- | ---: | ---: |
| `quantum-recoverability-pqs` | `a7467f5a5553aac325052eb7e0c5570c15f395fe` | 2 | 34 |
| `quantum-recoverability-aerie` | `f86c13791ed68b60fc5b3114f50d5d50c8f9234b` | 3 | 34 |

¹ Includes source, tests, documentation, binary fixtures, retained logs and original references.
Core paths: **16 added / 18 modified / 0 deleted**. Generated comparison/provenance
metadata is excluded from these counts. Large evidence files are not additional runtime features.
The [complete path inventory](MAINNET_COMPARISON.json) records status, category, blob/SHA256 and
committed/uncommitted layers for every path, including the Aerie variant.

## Changes and reasons

| Area | Change from mainnet | Reason | Main paths |
| --- | --- | --- | --- |
| VM/dependency integration | Replace upstream’s git-pinned snarkVM with the matching rebased sibling source; add recovery/core, records, signatures, exact crypto/encoding dependencies and locked graph. | Node and VM must use the same transaction/state implementation; the vanilla upstream dependency cannot parse the new Recovery variant. | `Cargo.*`; `cli/Cargo.toml`; `node/rest/Cargo.toml` |
| Startup capabilities | Check consensus-required typed backend capabilities before ledger use. | Capable scheduled nodes can start; missing implementations fail clearly instead of accepting incompatible state. | `cli/src/helpers/recovery.rs`; `cli/src/commands/start.rs` |
| Historical dev genesis | Test-network-only genesis file/hash options validate canonical bytes, genesis rules, signer and configured committee. | Exercise genuine old V0 records on a repeatable localhost network without rewriting records or weakening public genesis rules. | `cli/src/commands/start.rs` |
| Developer tooling | Recovery key generation, source extraction, canonical envelope construction, enrollment/rotation/migration/spending and test mutations. | Produce actual signed transactions and owner witnesses for the production path; keep key files external. | `cli/src/commands/developer/{mod,recovery}.rs` |
| Consensus queues | Admit Recovery alongside execution transactions and classify it explicitly. | Prevent new transactions being misrouted as deployments or dropped before BFT inclusion. | `node/consensus/src/{lib,transactions_queue}.rs` |
| REST and client propagation | Expose public recovery state; apply verification slots to Recovery, normal broadcast/admission, and client receive/queue/forward handling. | Keep wallet state, candidate-height verification and propagation consistent across validator and client roles. | `node/rest/src/{lib,routes}.rs`; `node/src/client/router.rs` |
| Message versions | Associate V23/V24 with message versions 34/35 and test activation boundaries; unscheduled versions do not become the public latest. | Synchronize wire compatibility with consensus activation while retaining current public behavior. | `node/router/messages/src/lib.rs` |
| Measured partitions | Suppress automatic validator discovery and bootstrap discovery tasks when trusted-peers-only is set, with deterministic regression coverage. | The test flag must enforce an observable 2+2 topology rather than reconnecting hidden peer links. | `node/bft/src/gateway.rs` |
| Metrics | Register/count accepted Recovery outcomes separately. | Account for the new confirmed variant without mislabeling it or breaking exhaustive metrics handling. | `node/metrics/src/{lib,names}.rs` |
| End-to-end tooling | Four-validator activation/migration/spend/fault/partition/replay tests, two-client relay, independent checkpoint decoder and hash-verified per-node proof/state caches. | Verify actual accepted bodies and accounting; block lookup alone also indexes aborted IDs. Reuse costly historical proofs safely. | `scripts/test_disclosed_recovery.py`; `scripts/recovery_{checkpoint,resume}.py` |
| Harness reliability | Per-endpoint read/broadcast pacing, bounded HTTP429 retries for reads only, checked continuation and evidence-integrity regressions. | Keep default REST limits and strict rejection assertions; never retry POSTs; preserve failed attempts and recheck accepted history. | `scripts/test_recovery_harness.py`; `scripts/test_disclosed_recovery.py` |
| Earlier preparation tests | Retain quantum-preparation smoke tests and unavailable-backend startup cases. | Separate earlier helper/startup evidence from the later transaction-bearing acceptance criteria. | `scripts/test_quantum_recoverability.py`; `scripts/test_recovery_backend_unavailable.py`; `recovery-evidence/` |
| Tooling and reports | Pin rustfmt nightly-2026-04-02, ignore Python cache files, retain architecture/results/source identities and exact reproduction evidence. | Keep local checks deterministic and distinguish tested behavior from assumptions. | `scripts/lint.sh`; `.gitignore`; `QUANTUM_RECOVERABILITY.md`; `RECOVERY_*.md` |

## Additional Aerie branch changes

The derived worktree changes VM/recovery dependency paths to `../snarkVM-aerie`, keeps the matching lock graph and adapts documentation/source identities. It inherits the same node integration; the Aerie adapter lives in snarkVM. Native policy still uses direct signatures, so a separate Aerie ownership/consensus path is not introduced.

## Behavior and compatibility

Before H1, public behavior stays on the scheduled legacy protocol. H1 requires owner-authorized
V0/V1→native V2 preparation and freezes legacy record paths/private fees. From H2, enrolled PQ
keys authorize V2→V3 migration and V3 spending; legacy transaction authorization is retired.
Dormant unprepared records freeze. Both public heights remain **unscheduled**.

The earlier console/circuit V2 hashing helpers and the new native disclosed V2/V3 credits
records are distinct. Neither a SHA3 opening nor an Aerie aggregate independently authenticates
historical ownership. Native preparation uses a classical owner/serial certificate before compromise;
subsequent effects require the previously enrolled PQ key. Validators never manufacture witnesses.
Schema additions preserve old data through the tested forward upgrade; database downgrade and
arbitrary finalized-chain reorganization are unsupported. Classical consensus/header/index and
checkpoint-authentication assumptions remain; this is not a quantum-safe validator network.

## Build and validation

The recovery branches now descend from the exact upstream revisions above. The uncommitted
implementation was restored after the rebase; backups and source archives are retained.
The rebase preserved the upstream proposal-spend multiplier wiring, updated lockfile versions,
VM history mapping spelling and CUDA target. See [the rebase report](UPSTREAM_REBASE.md) for
conflict resolutions, source preservation checks and the current fresh-validation status.

**Fresh post-rebase validation passed:** all four strict checks, recovery/signature/historical-record
tests, the independent model, the BFT suite, a rebuilt node, ordinary eight-node CI and a new
transaction-bearing four-validator recovery run with two-client relay. Exact commands, source/binary
identities and limits are in [UPSTREAM_REBASE.md](UPSTREAM_REBASE.md).
Older [results](RECOVERY_RESULTS.md) retain **pre-rebase evidence**, including suites not rerun here;
their previous binary hashes, timings and pass counts are not fresh results for these revisions.

Reproduce this comparison without checking out or modifying the current branches:

```sh
git fetch --no-tags upstream refs/heads/mainnet:refs/remotes/upstream/mainnet
git diff d30bf265c64ef9c6624d8b6859040be88030b901 HEAD --stat       # committed layer
git diff d30bf265c64ef9c6624d8b6859040be88030b901 --name-status    # tracked working tree
git ls-files --others --exclude-standard            # also include new files
```

The JSON pins the fetched SHA so later mainnet movement cannot silently change this report.
Current source identities are in [RECOVERY_SOURCE_MANIFEST.json](RECOVERY_SOURCE_MANIFEST.json);
pre-rebase and rebased-candidate source snapshots, preservation records and validation logs are retained under
`/workspace/security-evaluation/upstream-rebase-20261106/`.

Remaining work: independent cryptographic/implementation audit, PQ-authenticated checkpoint transport,
quantum-safe consensus, public-scale state/fee qualification, arbitrary-program support and a
consensus-gated policy upgrade to schedule H2 after enrollment. Genuine rejected-private-fee source
preparation is structurally supported but untested. The restored overlay remains uncommitted. No live deployment or remote push is included.
