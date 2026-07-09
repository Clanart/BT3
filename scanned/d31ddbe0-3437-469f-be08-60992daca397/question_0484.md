# Q484: NEAR near_withdraw_callback asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `callback after unwrapping wNEAR during public payouts` with control over recipient account, amount, and success/failure of the preceding wNEAR withdrawal and desynchronize `near/omni-bridge/src/lib.rs::near_withdraw_callback` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::near_withdraw_callback` and the adjacent mint, burn, or custody accounting after every branch.
