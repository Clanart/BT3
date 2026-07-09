# Q1552: NEAR OmniToken ft_transfer_call callback-bearing token flow exposes inconsistent intermediate state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public token transfer-call entrypoint on wrapped Near tokens` and then replay or reorder later callback or refund resolution so that `near/omni-token/src/lib.rs::ft_transfer_call` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback-bearing token flow exposes inconsistent intermediate state` under delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
