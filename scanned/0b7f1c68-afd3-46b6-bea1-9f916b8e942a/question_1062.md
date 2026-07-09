# Q1062: NEAR OmniToken ft_transfer_call custody accounting diverges from wrapped supply through cross-module drift

## Question
Can an unprivileged attacker use `public token transfer-call entrypoint on wrapped Near tokens` with control over receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract and desynchronize `near/omni-token/src/lib.rs::ft_transfer_call` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `custody accounting diverges from wrapped supply` attack class because delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_transfer_call` and the adjacent mint, burn, or custody accounting after every branch.
