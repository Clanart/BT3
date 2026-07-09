# Q1982: NEAR update_transfer_fee native versus wrapped branch switch

## Question
Can an unprivileged attacker choose inputs to `public `update_transfer_fee` on pending outbound transfer` that make `near/omni-bridge/src/lib.rs::update_transfer_fee` classify the asset differently before and after a custody-changing step through rewrites `transfer.message.fee` on an existing pending transfer after checking `origin_transfer_id`, sender restrictions, and attached deposit equality for native-fee deltas, violating `fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::update_transfer_fee`
- Entrypoint: `public `update_transfer_fee` on pending outbound transfer`
- Attacker controls: pending transfer id, replacement token fee, replacement native fee, attached deposit, and caller identity as sender or non-sender
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates.
- Invariant to test: fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models.
