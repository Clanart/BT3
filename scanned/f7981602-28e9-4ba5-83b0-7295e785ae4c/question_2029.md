# Q2029: NEAR OmniToken mint custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `public bridge-token mint path via controller-only callback reached from bridge delivery` to make `near/omni-token/src/lib.rs::mint` increase wrapped supply or reduce custody without the complementary change on the other side, violating `mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes`?

## Target
- File/function: `near/omni-token/src/lib.rs::mint`
- Entrypoint: `public bridge-token mint path via controller-only callback reached from bridge delivery`
- Attacker controls: recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call`
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
