# Q1871: NEAR OmniToken mint mint-with-message path differs economically from plain mint at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-token mint path via controller-only callback reached from bridge delivery` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::mint` violate `mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes` in the `mint-with-message path differs economically from plain mint` attack class because controller-only mint either deposits directly or first credits the predecessor account then calls `ft_transfer_call` to the recipient when `msg` is present becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::mint`
- Entrypoint: `public bridge-token mint path via controller-only callback reached from bridge delivery`
- Attacker controls: recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call`
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
