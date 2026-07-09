# Q3583: NEAR update_transfer_fee fee payout and storage refund overlap at boundary values

## Question
Can an unprivileged attacker trigger `public `update_transfer_fee` on pending outbound transfer` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::update_transfer_fee` violate `fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored` in the `fee payout and storage refund overlap` attack class because rewrites `transfer.message.fee` on an existing pending transfer after checking `origin_transfer_id`, sender restrictions, and attached deposit equality for native-fee deltas becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::update_transfer_fee`
- Entrypoint: `public `update_transfer_fee` on pending outbound transfer`
- Attacker controls: pending transfer id, replacement token fee, replacement native fee, attached deposit, and caller identity as sender or non-sender
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
