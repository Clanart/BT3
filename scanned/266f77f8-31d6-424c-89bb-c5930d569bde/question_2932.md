# Q2932: NEAR OmniToken ft_transfer_call global asset-conservation invariant break through cross-module drift

## Question
Can an unprivileged attacker use `public token transfer-call entrypoint on wrapped Near tokens` with control over receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract and desynchronize `near/omni-token/src/lib.rs::ft_transfer_call` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `global asset-conservation invariant break` attack class because delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_transfer_call` and the adjacent mint, burn, or custody accounting after every branch.
