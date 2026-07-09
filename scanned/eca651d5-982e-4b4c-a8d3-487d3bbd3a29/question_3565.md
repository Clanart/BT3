# Q3565: NEAR near_withdraw_callback different callback outcomes produce the same user-visible success through cross-module drift

## Question
Can an unprivileged attacker use `callback after unwrapping wNEAR during public payouts` with control over recipient account, amount, and success/failure of the preceding wNEAR withdrawal and desynchronize `near/omni-bridge/src/lib.rs::near_withdraw_callback` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `different callback outcomes produce the same user-visible success` attack class because sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::near_withdraw_callback` and the adjacent mint, burn, or custody accounting after every branch.
