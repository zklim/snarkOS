# Recovery evaluation results

The private-proof follow-up is in
[`RECOVERY_OWNERSHIP_STATUS.md`](RECOVERY_OWNERSHIP_STATUS.md). New proof-backend
qualification and model tests do not establish snarkOS migration acceptance or
durable recovery storage.

Scope: public-value experimental recovery state machine, real direct PQ
signatures and a separately pinned Aerie adapter. **No private Aleo recovery
transaction was accepted on a validator devnet.** No live deployment occurred.
Source, pins, encoding and trust boundaries are in `RECOVERY_ARCHITECTURE.md`.

| Claim | Evidence | Boundary / assumption |
| --- | --- | --- |
| Core owns the complete authorization statement | Independent Python encoding/hash vectors; cross-chain, policy, fee, output/key, replay and context mutations | SHA3-512 assumptions; not a proof of private ownership |
| Two-stage rules and public-value conservation | 12 conformance tests, including 128 independent activation-table cases; V0/V1 preparation, rotation, V2 migration and V3 spending with both schemes | Trusted public source import; only experimental credits |
| Enrolled authority cannot be substituted or downgraded | Old-key authorization + recipient possession; migration preserves key; lower category outputs rejected even through spending | A stolen enrolled PQ secret still authorizes spending |
| Atomic application and replay/spend exclusion | Forged/repeated migrations and competing candidate spends reject without state mutation; canonical checkpoint restoration reproduces state; copied nonce regression failed before the fix and passes after it | In-memory core only; production indexes, disk crash atomicity and reorgs not integrated |
| Genuine signature substitutability | ML-DSA-65 and Falcon-512 run the same ledger code; NIST ACVP ML-DSA-65 case 44 passes | Not implementation certification; no independent external positive Falcon vector |
| Exact Aerie batch semantics | Direct/aggregate differential acceptance; ordering, omissions, duplicates, changed messages, malformed proofs, salt/profile/header mutations reject; no fallback | Research Aerie/PCS assumptions; no QROM or zero-knowledge guarantee |
| Decoder handles adversarial bytes | Final libFuzzer/ASan run: 905,746 executions / 31 seconds, coverage 367, features 457, no failure; two property tests | Effects codec only; not exhaustive ledger or aggregate-proof fuzzing |
| Existing V2 helpers retain native/circuit consistency | 11 native and 7 circuit tests pass | These are the existing preparation helpers, not new private ownership-proof constraints |
| Missing integration fails clearly | Four local processes all exit 1 before ledger creation under schedule 30/40 | Negative startup test, not a connected four-validator network |
| Unscheduled node remains usable | Local client serves genesis; persisted restart returns the same block hash; both activation heights remain MAX | Isolated client smoke test, not transaction or partition validation |

Validation: snarkOS full workspace/all-target/all-feature strict Clippy passed.
Affected recovery crates' strict Clippy passed. The optional VM-wide all-feature
Clippy run was interrupted during an unrelated fresh native RocksDB build; no
full VM-wide pass is claimed. Aerie strict Clippy, verifier-only build, shared 12-test suite and differential test
passed. Both worktrees pass pinned formatting. The verifier-only dependency graph
excludes Aerie prover and witness crates.

Measured samples used Rust 1.96.1, optimized test profile, four build jobs on a
shared four-CPU cloud environment. They are test samples, not production capacity:

| Path | Bytes | Verification / production |
| --- | --- | --- |
| ML-DSA-65, one 4,476-byte statement | PK 1,952; signature 3,309 | Mean verification 822 microseconds over 100 calls |
| Falcon-512, one 2,366-byte statement | PK 897; signature 666 | Mean verification 31 microseconds over 100 calls |
| Two direct Falcon authorizations | 1,332 signature bytes | 57 microseconds for the sampled batch |
| Aerie over the same two authorizations | 60,280 proof bytes | 20 ms verification, 385 ms production including setup and direct prechecks |

This small batch costs more with aggregation. Timings vary under compile/test
contention; no large-batch scaling, memory ceiling or throughput claim is made.

Remaining work includes the private ownership/membership/conservation proof,
authenticated on-chain V2 enrollment, native/circuit binding, production transaction
and fee/program integration, shared old/new spend exclusion, atomic storage and
rollback, and transaction-bearing multi-validator activation/partition/restart
validation. Aerie proof soundness, parameter assumptions and crypto implementations
require independent cryptographic review. Existing encryption and BFT remain
classical.
