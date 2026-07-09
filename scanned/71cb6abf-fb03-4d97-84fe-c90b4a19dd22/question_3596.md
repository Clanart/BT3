# Q3596: NEAR claim_fee callback fast path and normal path can both pay at boundary values

## Question
Can an unprivileged attacker trigger `proof callback reached from public `claim_fee`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::claim_fee_callback` violate `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer` in the `fast path and normal path can both pay` attack class because removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
