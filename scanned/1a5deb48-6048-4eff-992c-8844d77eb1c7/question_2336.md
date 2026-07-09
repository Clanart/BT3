# Q2336: NEAR OmniToken ft_transfer_call different callback outcomes produce the same user-visible success through cross-module drift

## Question
Can an unprivileged attacker use `public token transfer-call entrypoint on wrapped Near tokens` with control over receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract and desynchronize `near/omni-token/src/lib.rs::ft_transfer_call` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `different callback outcomes produce the same user-visible success` attack class because delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_transfer_call` and the adjacent mint, burn, or custody accounting after every branch.
