# Q2333: NEAR OmniToken mint custody accounting diverges from wrapped supply through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-token mint path via controller-only callback reached from bridge delivery` with control over recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call` and desynchronize `near/omni-token/src/lib.rs::mint` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `custody accounting diverges from wrapped supply` attack class because controller-only mint either deposits directly or first credits the predecessor account then calls `ft_transfer_call` to the recipient when `msg` is present, violating `mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes`?

## Target
- File/function: `near/omni-token/src/lib.rs::mint`
- Entrypoint: `public bridge-token mint path via controller-only callback reached from bridge delivery`
- Attacker controls: recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call`
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Also assert cross-module consistency between `near/omni-token/src/lib.rs::mint` and the adjacent mint, burn, or custody accounting after every branch.
