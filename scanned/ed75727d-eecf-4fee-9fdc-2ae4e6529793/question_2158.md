# Q2158: NEAR send_tokens helper native versus wrapped branch switch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public finalize and fast paths` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::send_tokens` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped branch switch` under chooses between wNEAR unwrap, bridge-token mint, `ft_transfer`, and `ft_transfer_call` depending on token type and message presence, violating `asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens`
- Entrypoint: `internal helper reached from public finalize and fast paths`
- Attacker controls: token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
