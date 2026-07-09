# Q674: NEAR update_transfer_fee fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public `update_transfer_fee` on pending outbound transfer` with crafted amount, fee, or native-fee inputs and make `near/omni-bridge/src/lib.rs::update_transfer_fee` use inconsistent fee and principal values across rewrites `transfer.message.fee` on an existing pending transfer after checking `origin_transfer_id`, sender restrictions, and attached deposit equality for native-fee deltas, violating `fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::update_transfer_fee`
- Entrypoint: `public `update_transfer_fee` on pending outbound transfer`
- Attacker controls: pending transfer id, replacement token fee, replacement native fee, attached deposit, and caller identity as sender or non-sender
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
