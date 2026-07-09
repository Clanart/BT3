# Q3326: NEAR claim_fee callback fast path and normal path can both pay via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback reached from public `claim_fee`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::claim_fee_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path and normal path can both pay` under removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee, violating `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
