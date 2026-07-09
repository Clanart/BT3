# Q1315: NEAR remove_transfer_message flows fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `public sign/finalize/claim callbacks through transfer cleanup` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` violate `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped` in the `fee and principal split divergence` attack class because cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
