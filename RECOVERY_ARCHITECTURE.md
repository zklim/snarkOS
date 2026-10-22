# Recovery integration status

The private-proof follow-up and remaining acceptance gaps are documented in
[`RECOVERY_OWNERSHIP_STATUS.md`](RECOVERY_OWNERSHIP_STATUS.md). No production
migration capability or transaction-bearing devnet pass is claimed.

The modular core is in the sibling snarkVM checkout's `RECOVERY_ARCHITECTURE.md`.
Both repositories have local `quantum-recoverability-pqs` worktrees; the
`quantum-recoverability-aerie` worktrees are at `../snarkVM-aerie` and
`../snarkOS-aerie`. The Aerie worktree adds the pinned aggregate adapter outside
the recovery core. Each Aerie branch descends from its core branch. The containing
Git commit identifies the published revision; this is not a completed production
recovery integration.

snarkOS's production VM dependency remains pinned to
`c493bc514f0850d4e07a0135b0e4f845e38d0c73`. This pins the existing preparation
feature. The new experimental recovery ledger is not used to verify or finalize
production Aleo transactions. It must not be advertised as private Aleo recovery.

The CLI checks recovery capabilities before genesis, ledger storage or peer setup.
Any scheduled V23/V24 fails with a missing consensus-integrated backend error.
The error is the same with direct or Aerie adapters installed: signature support
does not supply the missing ownership-proof and atomic storage integration.
Published activation heights remain unscheduled. No live network was deployed.

```sh
cargo build --locked --bin snarkos --features test_network
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo +nightly-2026-04-02 fmt --all -- --check
python3 scripts/test_recovery_backend_unavailable.py
```

The script starts four isolated processes with the proposed 30/40 test schedule
and requires all to refuse startup before creating a ledger. This is a negative
capability test, **not** a transaction-bearing recovery devnet. The older
`scripts/test_quantum_recoverability.py` exercises the earlier empty-block
preparation experiment; the new startup guard intentionally prevents that run.
Its earlier results in `QUANTUM_RECOVERABILITY.md` are historical, not evidence for
this implementation.

Still required for a functioning Aleo recovery devnet: a consensus transaction
format; authenticated V2 output enrollment and native/circuit constraints; a
specified private ownership/membership/conservation proof; shared old/new spent
indexes; fee/program integration; atomic persistent block updates and rollback;
and networking/admission support. Only after those exist can activation,
transaction-bearing partitions, restarts and reconnection be validated honestly.
