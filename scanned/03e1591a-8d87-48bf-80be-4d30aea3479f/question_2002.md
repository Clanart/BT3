# Q2002: NEAR finish_withdraw_v2 native versus wrapped branch switch

## Question
Can an unprivileged attacker choose inputs to `legacy/public withdrawal completion path on migrated tokens` that make `near/omni-bridge/src/lib.rs::finish_withdraw_v2` classify the asset differently before and after a custody-changing step through accepts calls only from deployed-token contracts, allocates new origin and destination nonces, stores an outbound transfer, and logs an `InitTransfer` event to Ethereum, violating `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models.
