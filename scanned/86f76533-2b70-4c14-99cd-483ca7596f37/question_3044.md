# Q3044: NEAR claim_fee callback final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `proof callback reached from public `claim_fee`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::claim_fee_callback` violate `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer` in the `final settlement and later fee claim can diverge` attack class because removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
