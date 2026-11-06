# Disclosed ownership integration

Local functional acceptance passes: genuine historical V0/V1 records are owner-prepared into
algorithm-bound native V2 outputs, migrated to V3 and spent under enrolled ML-DSA/Falcon keys.
Four-validator persistence/fault/partition/snapshot tests and the two-client relay pass.
[Results, evidence and limits](RECOVERY_RESULTS.md) distinguish the checked HTTP429 continuation
from the original interrupted invocation.

The owner relation verifies disclosed commitments, actual confirmed origin, deterministic
consumption, program semantics, conservation and exact effects. Preparation retains a classical
owner/serial certificate before compromise. Subsequent authorization requires the enrolled PQ key;
a digest opening or Aerie aggregate is never a replacement for it. No account seed or PQ secret
is serialized into recovery transactions.

Native V2/V3 are distinct disclosed credits records. Other programs and unprepared dormant records
are unsupported/frozen as specified. Authentic pre-compromise enrollment and continuing ledger
state are assumptions; classical consensus and checkpoint transport are not upgraded by PQ keys.
The optional pinned Aerie adapter remains separate. Public heights are unscheduled; no live deployment.

[Complete ownership statement](../snarkVM-aerie/recovery/OWNERSHIP_PROOF.md).
[Backend assumptions](../snarkVM-aerie/recovery/PROOF_BACKENDS.md).
[Source identities and reproduction](../snarkVM-aerie/recovery/INTEGRATION_STATUS.md).
