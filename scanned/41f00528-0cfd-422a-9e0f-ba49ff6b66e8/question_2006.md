# Q2006: NEAR send_tokens helper native versus wrapped branch switch

## Question
Can an unprivileged attacker choose inputs to `internal helper reached from public finalize and fast paths` that make `near/omni-bridge/src/lib.rs::send_tokens` classify the asset differently before and after a custody-changing step through chooses between wNEAR unwrap, bridge-token mint, `ft_transfer`, and `ft_transfer_call` depending on token type and message presence, violating `asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens`
- Entrypoint: `internal helper reached from public finalize and fast paths`
- Attacker controls: token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates.
- Invariant to test: asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models.
