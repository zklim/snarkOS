# Private recovery follow-up — 2026-10-22

**Incomplete: no production private migration or transaction-bearing recovery
devnet acceptance.** This follow-up preserves the existing snarkOS code, pinned VM
dependency and V23/V24 startup guard. Public activation heights are unscheduled.
No live deployment occurred.

The required private ownership construction remains blocked. An actual pinned
Winterfell proof experiment extracted private witnesses from serialized valid
proofs. RISC Zero and SP1 remain unqualified for the complete private, quantum-sound
Aleo relation in this review. Their generic computation capability and PQ
signatures do not establish that relation. This is not a claim that a suitable
proof construction is impossible.

The detailed construction, backend/source assessment and acceptance matrix are
in the matching snarkVM worktree:

- `recovery/OWNERSHIP_PROOF.md`
- `recovery/PROOF_BACKENDS.md`
- `recovery/INTEGRATION_STATUS.md`
- `recovery/evidence/ownership-20261022/`

New independent model evidence covers 127,185 states with six passing invariants,
five required faulty-rule counterexamples and three progress reachability checks.
It is a bounded target specification; it does not test snarkOS storage or network
transactions. The genuine ML-DSA/Falcon conformance suite (12 tests) and existing
Aerie direct/aggregate differential test pass again. The ownership-backend privacy
probe passes its negative qualification assertions. Logs are retained in snarkVM.

snarkOS bases: `5d497bf3253eec606404bee8d65c305d8cd86f76` on
`quantum-recoverability-pqs` and `quantum-recoverability-aerie`. Both still pin VM
`c493bc514f0850d4e07a0135b0e4f845e38d0c73`. VM core and Aerie worktrees retain
their separate dependency graphs. Each Aerie branch descends from its core branch;
Git history identifies the published revisions and source ancestry.

The transaction enum, proof/circuit, fee program, speculation/finalization and
durable consumption/enrollment storage still need integration. The node cannot
truthfully start a recovery test schedule until that complete path works. Removing
the guard would permit startup without adding any accepted migration path.

No four-validator transaction/partition/restart/crash/reorg result is claimed.
Prior refusal or empty-block results do not satisfy these acceptance criteria.
Reproducible commands, raw proof/model results, exact source identities and
before/after snapshots are retained at
`/workspace/security-evaluation/ownership-integration-20261022`.
