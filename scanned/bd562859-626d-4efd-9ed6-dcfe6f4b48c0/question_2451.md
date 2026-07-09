# Q2451: NEAR claim_fee callback delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `proof callback reached from public `claim_fee`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::claim_fee_callback` violate `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer` in the `delivery callback leaves inconsistent state` attack class because removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
