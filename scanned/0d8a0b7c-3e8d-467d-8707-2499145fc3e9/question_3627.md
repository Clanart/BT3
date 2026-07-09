# Q3627: NEAR OmniToken mint different callback outcomes produce the same user-visible success at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-token mint path via controller-only callback reached from bridge delivery` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::mint` violate `mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes` in the `different callback outcomes produce the same user-visible success` attack class because controller-only mint either deposits directly or first credits the predecessor account then calls `ft_transfer_call` to the recipient when `msg` is present becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::mint`
- Entrypoint: `public bridge-token mint path via controller-only callback reached from bridge delivery`
- Attacker controls: recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call`
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
