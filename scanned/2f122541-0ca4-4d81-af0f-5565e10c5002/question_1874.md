# Q1874: NEAR OmniToken ft_transfer_call callback-bearing token flow exposes inconsistent intermediate state at boundary values

## Question
Can an unprivileged attacker trigger `public token transfer-call entrypoint on wrapped Near tokens` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::ft_transfer_call` violate `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event` in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
