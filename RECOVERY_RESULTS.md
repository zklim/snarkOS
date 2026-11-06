> Post-rebase validation: see [UPSTREAM_REBASE.md](UPSTREAM_REBASE.md). The results below retain pre-rebase evidence, including suites not rerun for this sync.

# Two-stage recovery: local validation passed

Four validators completed both activations, historical V0/V1 preparation, V2→V3 migration,
PQ spending, negative admission, persistence, interrupted finalization, measured 2+2 partition,
reconnection and full snapshot replay. Two additional clients relayed an accepted native spend;
all six nodes agreed. The composed run includes a checked continuation after HTTP429 at the
last post-rollback read; the original interruption is retained.

Twelve accepted native transactions conserve 3,000,000,000 microcredits as 2,998,800,000
unspent plus 1,200,000 fees. Unchanged ordinary devnet CI passes in 208.19 seconds,
including all eight nodes and original log caps. Strict checks and pinned formatting pass.

[Complete construction, claim-to-evidence matrix, transactions, costs and commands](../snarkVM-aerie/RECOVERY_RESULTS.md).
[Source identities](RECOVERY_SOURCE_MANIFEST.json).

Public activation heights remain unscheduled. No live network was deployed. This is the
experimental disclosed credits profile; classical consensus/checkpoint assumptions, unsupported
programs, later policy upgrades, scaling and independent cryptographic review remain explicit
limitations. The separately derived Aerie adapter is optional and disabled in native policy.
