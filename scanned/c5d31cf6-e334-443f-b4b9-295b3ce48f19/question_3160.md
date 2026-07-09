# Q3160: NEAR near_withdraw_callback callback-bearing token flow exposes inconsistent intermediate state at boundary values

## Question
Can an unprivileged attacker trigger `callback after unwrapping wNEAR during public payouts` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::near_withdraw_callback` violate `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded` in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
