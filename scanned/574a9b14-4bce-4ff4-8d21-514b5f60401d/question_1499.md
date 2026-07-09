# Q1499: NEAR update_transfer_fee storage payer or owner spoofing via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `update_transfer_fee` on pending outbound transfer` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::update_transfer_fee` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage payer or owner spoofing` under rewrites `transfer.message.fee` on an existing pending transfer after checking `origin_transfer_id`, sender restrictions, and attached deposit equality for native-fee deltas, violating `fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::update_transfer_fee`
- Entrypoint: `public `update_transfer_fee` on pending outbound transfer`
- Attacker controls: pending transfer id, replacement token fee, replacement native fee, attached deposit, and caller identity as sender or non-sender
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
