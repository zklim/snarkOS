# Two-stage disclosed recovery integration

The current scope is quantum recoverability and authorization correctness.
Record data, values, openings and membership information may be public; reusable
account and PQ signing secrets remain secret. No confidentiality, zero-knowledge
or unlinkability guarantee is claimed. Public activations remain unscheduled;
no live network deployment is performed.

The complete construction, canonical statement and trust boundaries are in the
matching [snarkVM architecture](../snarkVM/RECOVERY_ARCHITECTURE.md) and
[ownership relation](../snarkVM/recovery/OWNERSHIP_PROOF.md). Preparation verifies
exact historical credits openings and source provenance, an owner signature and
a classical certificate binding the original serial. It enrolls an exact PQ
algorithm/profile/key before legacy compromise. After H2, only that enrolled
PQ authority can authorize migration/spending; disclosed openings do not grant
replacement authority. Continuing authenticated ledger state is an explicit
assumption. This feature does not upgrade classical validator consensus or supply
independently PQ-authenticated checkpoint transport. A local SHA3 root alone
is not a trustworthy anchor. Recovery transaction IDs also retain the classical
BHP Merkle index over a field-truncated SHA3 envelope leaf; native full-width
effects/state authorization does not make that index or block identity PQ.

The new native `Transaction::Recovery` travels through normal networking,
admission, candidate-height verification, ordered speculation, finalization and
persistent archival. Native V2/V3 are a distinct disclosed credits class, not
legacy private circuit ciphertexts. Shared exclusion and atomic state changes
pass the baseline VM and durable storage tests. Subsequent exact confirmed-body
source-membership checks pass focused regressions and both core strict lint/
formatting checks. The source-matched optimized build and transaction-bearing four-validator validation pass,
including checked snapshot replay and a two-client relay.

| Candidate height | Record policy |
| --- | --- |
| Before H1 | Historical legacy behavior; no native recovery |
| H1 through H2−1 | Owner-authorized V0/V1→V2 preparation and V2 rotation; freeze legacy record operations/private fees |
| H2 onward | Unprepared records frozen; V2→V3 with enrolled key; V3 spending/rotation; legacy authorization retired |

The local activation profile fixes both heights consistently before the first
native enrollment. Checkpoints bind that complete policy. Scheduling a previously
unscheduled H2 after V2 state exists requires a separate consensus policy and
checkpoint upgrade; this implementation rejects a mismatched policy instead of
changing authenticated state when a node installs new software.

The core branches are `quantum-recoverability-pqs` in both repositories. Separate
`quantum-recoverability-aerie` branches derive from them and retain pinned Aerie
adapters outside ownership/state logic. Genuine ML-DSA and Falcon authorization
share the transition logic. Optional Aerie aggregation is not enabled by native
production policy; its classical-ROM argument is not relabeled quantum soundness.
Exact starting commits, dependencies and source snapshots are recorded in the
[VM integration status](../snarkVM/recovery/INTEGRATION_STATUS.md).

snarkOS currently uses the matching local VM tree for development. Reproduction
must include that exact tree/snapshot and both lockfiles. A starting commit alone
does not identify later uncommitted changes. The native VM production-cycle test
now passes, including both activation heights, archival, shared consumption and
atomic aborts. Startup checks the typed consensus-required capabilities;
incompatible implementations fail before accepting state. Four-validator transaction acceptance and the additional client relay now pass.

The developer recovery commands and `scripts/test_disclosed_recovery.py` supply
local key/envelope generation and the transaction-bearing four-validator harness.
Actual accepted transactions, state and fault evidence are in [RECOVERY_RESULTS.md](RECOVERY_RESULTS.md).
