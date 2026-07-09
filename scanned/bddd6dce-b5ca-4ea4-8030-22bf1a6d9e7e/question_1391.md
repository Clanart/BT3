# Q1391: NEAR OmniToken ft_transfer_call callback-bearing token flow exposes inconsistent intermediate state

## Question
Can an unprivileged attacker exploit a callback-bearing branch in `public token transfer-call entrypoint on wrapped Near tokens` so that `near/omni-token/src/lib.rs::ft_transfer_call` exposes intermediate state that a receiver or token contract can act on inconsistently, violating `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof.
