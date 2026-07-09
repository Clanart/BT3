# Q563: NEAR OmniToken ft_transfer_call mint-with-message path differs economically from plain mint at boundary values

## Question
Can an unprivileged attacker trigger `public token transfer-call entrypoint on wrapped Near tokens` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::ft_transfer_call` violate `receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event` in the `mint-with-message path differs economically from plain mint` attack class because delegates directly to the fungible-token standard `ft_transfer_call` path used by bridge deliveries and receiver callbacks becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer_call`
- Entrypoint: `public token transfer-call entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and arbitrary `msg` delivered to the receiver contract
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: receiver-controlled callback semantics must never let a user both keep wrapped tokens locally and still obtain a cross-chain bridge claim for the same burn or mint event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
