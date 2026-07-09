# Q652: NEAR near_withdraw_callback asset-branch confusion on finalization at boundary values

## Question
Can an unprivileged attacker trigger `callback after unwrapping wNEAR during public payouts` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::near_withdraw_callback` violate `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded` in the `asset-branch confusion on finalization` attack class because sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
