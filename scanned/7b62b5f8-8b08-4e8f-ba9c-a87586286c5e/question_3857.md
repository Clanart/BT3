# Q3857: NEAR claim_fee callback stale or reordered proof acceptance via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback reached from public `claim_fee`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::claim_fee_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `stale or reordered proof acceptance` under removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee, violating `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
