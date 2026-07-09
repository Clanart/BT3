# Q2032: NEAR OmniToken ft_transfer_call different callback outcomes produce the same user-visible success

## Question
Can an unprivileged attacker use `public token transfer-call entrypoint on wrapped Near tokens` so that `near/omni-token/src/lib.rs::ft_transfer_call` treats materially different callback outcomes as the same economic result because of delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition.
