# Q3742: NEAR send_tokens helper delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `internal helper reached from public finalize and fast paths` that causes `near/omni-bridge/src/lib.rs::send_tokens` to keep or remove settlement state inconsistently with delivered value because of chooses between wNEAR unwrap, bridge-token mint, `ft_transfer`, and `ft_transfer_call` depending on token type and message presence, violating `asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens`
- Entrypoint: `internal helper reached from public finalize and fast paths`
- Attacker controls: token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
