# Q2: NEAR update_transfer_fee burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public `update_transfer_fee` on pending outbound transfer` to force `near/omni-bridge/src/lib.rs::update_transfer_fee` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via rewrites `transfer.message.fee` on an existing pending transfer after checking `origin_transfer_id`, sender restrictions, and attached deposit equality for native-fee deltas, violating `fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::update_transfer_fee`
- Entrypoint: `public `update_transfer_fee` on pending outbound transfer`
- Attacker controls: pending transfer id, replacement token fee, replacement native fee, attached deposit, and caller identity as sender or non-sender
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: fee mutation must never let an attacker sign or claim a materially different transfer than the one users funded and stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
