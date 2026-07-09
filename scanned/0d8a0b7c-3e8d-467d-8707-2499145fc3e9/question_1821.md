# Q1821: NEAR update_transfer_fee storage payer or owner spoofing at boundary values

## Question
Can an unprivileged attacker trigger `public `update_transfer_fee` on pending outbound transfer` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::update_transfer_fee` violate `fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored` in the `storage payer or owner spoofing` attack class because rewrites `transfer.message.fee` on an existing pending transfer after checking `origin_transfer_id`, sender restrictions, and attached deposit equality for native-fee deltas becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::update_transfer_fee`
- Entrypoint: `public `update_transfer_fee` on pending outbound transfer`
- Attacker controls: pending transfer id, replacement token fee, replacement native fee, attached deposit, and caller identity as sender or non-sender
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
