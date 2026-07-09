# Q560: NEAR OmniToken mint asset-branch confusion on finalization at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-token mint path via controller-only callback reached from bridge delivery` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::mint` violate `mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes` in the `asset-branch confusion on finalization` attack class because controller-only mint either deposits directly or first credits the predecessor account then calls `ft_transfer_call` to the recipient when `msg` is present becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::mint`
- Entrypoint: `public bridge-token mint path via controller-only callback reached from bridge delivery`
- Attacker controls: recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call`
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
