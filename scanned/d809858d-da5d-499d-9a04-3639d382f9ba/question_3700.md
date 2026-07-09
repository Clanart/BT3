# Q3700: NEAR near_withdraw_callback different callback outcomes produce the same user-visible success at boundary values

## Question
Can an unprivileged attacker trigger `callback after unwrapping wNEAR during public payouts` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::near_withdraw_callback` violate `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded` in the `different callback outcomes produce the same user-visible success` attack class because sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
