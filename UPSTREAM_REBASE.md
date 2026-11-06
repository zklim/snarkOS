> Publication: see [RECOVERY_PUBLICATION.md](RECOVERY_PUBLICATION.md). The report below records the validated pre-publication snapshot.

# Recovery branches rebased onto upstream mainnet

The `upstream` remote is `aleo-labs-protocol` in each repository. Local `mainnet`
tracks `upstream/mainnet`; the existing `origin` fork and historical recovery branches are preserved.
Both core recovery branches were rebased, then their Aerie-only commits were replayed onto
those rebased cores. Original uncommitted files remain uncommitted.

| Worktree | Rebased HEAD | Upstream mainnet ancestor |
| --- | --- | --- |
| snarkVM | `31fff32e3393ab0596d31cd060376e5a15c78b73` | `c2909c55efb0c2202e7471e3e2efd73de1c4a468` |
| snarkOS | `a7467f5a5553aac325052eb7e0c5570c15f395fe` | `d30bf265c64ef9c6624d8b6859040be88030b901` |
| snarkVM-aerie | `63713683da4686092cbd2324d9265ee78fc207b9` | `c2909c55efb0c2202e7471e3e2efd73de1c4a468` |
| snarkOS-aerie | `f86c13791ed68b60fc5b3114f50d5d50c8f9234b` | `d30bf265c64ef9c6624d8b6859040be88030b901` |

Backups: `backup/before-upstream-20261106-pqs` and
`backup/before-upstream-20261106-aerie`, retained stashes and complete pre-rebase source
archives. Their exact IDs and restore records are in
`/workspace/security-evaluation/upstream-rebase-20261106/preserved-work.json`.

Conflicts were limited to snarkOS Cargo dependency declarations/lock sources and the
Aerie sibling path. Core uses `../snarkVM`; Aerie uses `../snarkVM-aerie`.
Upstream removal of the direct crypto-bigint pin and its newer dependency versions were
preserved. The new proposal-spend multiplier argument/default/validation remains wired
through CLI, node, consensus, BFT and primary. The VM history mapping spelling fix and
CUDA target update are preserved. Recovery logic, reserved unscheduled public activation
heights, state/authorization separation and Aerie revision pins are unchanged.

Immediately after restoring the original overlay, a preservation check found no changed
pre-existing path outside the upstream delta. Later report and harness updates are documented below.
Source parity and core dependency identities match between the paired core/Aerie variants.

## Validation

Fresh checks passed against these rebased sources:

| Check | Evidence |
| --- | --- |
| All four worktrees: pinned formatting and strict workspace/all-target/all-feature Clippy | `validation.json`, individual fmt/Clippy logs |
| Recovery core, genuine ML-DSA/Falcon conformance and historical record relations | 21 tests passed; 1 optional timing test ignored; `recovery-tests.log` |
| H1 parent/candidate cost boundary and harness regressions | 1 Rust cost test; 12 Python harness tests; corresponding logs |
| Independent bounded transition model | 1504 states, 1138 accepted edges, 11 mutation checks; `model/results.json` |
| BFT suite, then optimized `test_network` build, then unchanged ordinary CI | 131 BFT tests; 4 validators + 4 clients; `bft-units.log`, `build-after.json`, `ci/results.json` |
| Actual preparation, migration, rotation and PQ spending | 12 accepted native transactions; 21 negative cases × 4 validators returned HTTP422; 2 relay clients; both activation boundaries, persistence, interrupted finalization, measured partition/reconnection and canonical snapshot replay; `native-devnet-paced/results.json` |
| New upstream CLI policy | Five invalid proposal-limit configurations rejected; `upstream-cli-tests.json` |
| Source preservation, ancestry, core/Aerie parity and unscheduled public heights | Retained preservation/branch/parity/activation JSON records |

Rebuilt binary SHA256: `bc1fbc261656f02a93474ab65e175a3889f3affdce7af675fc21dbc03b26c4ca`. Rust/Cargo inputs were unchanged
during compilation and matched the candidate source archives. Compiler/profile, static RocksDB
and linker-wrapper identities are pinned in `build-environment.json`.

Elapsed checks include compilation: BFT 1134.9s,
optimized node build 586.1s. CI runtime: 169.2s;
the completed recovery devnet: 440.9s.
Historical proofs were reused from hash-verified fixtures and reverified on the fresh binary.
Two validation invocation/environment errors were corrected; all attempts remain in
`validation.json`. Neither required a recovery-source change.

The first recovery devnet stopped after accepted preparations when a negative broadcast hit
HTTP429 instead of reaching validation. That failed run remains in `native-devnet/`.
The harness now spaces reads and broadcasts at 0.2s per endpoint; POSTs are forwarded once,
with no automatic resubmission. Two regressions cover shared pacing and POST failure propagation.
Server limits and required structured HTTP422 rejections are unchanged. This Python-only test fix
is mirrored into the Aerie worktree and recorded separately from the rebase conflict resolutions.

Evidence root: `/workspace/security-evaluation/upstream-rebase-20261106/`.
`validation.json` records every exact command, environment override, exit code and log hash.
`rebased-candidate-sources/` pins build inputs; `sources-final/` retains the final documented trees.
Original source archives, backup branches and stashes remain available.

The Aerie variants were compiled and checked for shared-core/dependency parity. Aerie aggregate
proof execution, coverage-guided fuzzing and the larger storage/VM suites were not rerun for this sync;
their earlier results remain pre-rebase evidence. This rebase provides no new cryptographic audit
or quantum-safe consensus/checkpoint guarantee. Public activation heights remain unscheduled.
The restored implementation remains uncommitted; no remote push or live deployment was performed.
