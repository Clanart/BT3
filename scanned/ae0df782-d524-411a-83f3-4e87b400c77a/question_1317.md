# Q1317: NEAR near_withdraw_callback delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `callback after unwrapping wNEAR during public payouts` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::near_withdraw_callback` violate `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded` in the `delivery callback leaves inconsistent state` attack class because sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
