# Q2184: NEAR OmniToken ft_transfer_call different callback outcomes produce the same user-visible success via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public token transfer-call entrypoint on wrapped Near tokens` and then replay or reorder later callback or refund resolution so that `near/omni-token/src/lib.rs::ft_transfer_call` ends up accepting two inconsistent interpretations of the same economic event specifically around `different callback outcomes produce the same user-visible success` under delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
