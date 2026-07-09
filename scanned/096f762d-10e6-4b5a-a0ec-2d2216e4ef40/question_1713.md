# Q1713: NEAR OmniToken ft_transfer_call callback-bearing token flow exposes inconsistent intermediate state through cross-module drift

## Question
Can an unprivileged attacker use `public token transfer-call entrypoint on wrapped Near tokens` with control over receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract and desynchronize `near/omni-token/src/lib.rs::ft_transfer_call` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_transfer_call` and the adjacent mint, burn, or custody accounting after every branch.
