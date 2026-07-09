# Q2116: NEAR near_withdraw_callback custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `callback after unwrapping wNEAR during public payouts` to make `near/omni-bridge/src/lib.rs::near_withdraw_callback` increase wrapped supply or reduce custody without the complementary change on the other side, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
