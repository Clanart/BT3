# Q2908: NEAR send_tokens helper callback refund creates value gap through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reached from public finalize and fast paths` with control over token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external and desynchronize `near/omni-bridge/src/lib.rs::send_tokens` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `callback refund creates value gap` attack class because chooses between wNEAR unwrap, bridge-token mint, `ft_transfer`, and `ft_transfer_call` depending on token type and message presence, violating `asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens`
- Entrypoint: `internal helper reached from public finalize and fast paths`
- Attacker controls: token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::send_tokens` and the adjacent mint, burn, or custody accounting after every branch.
