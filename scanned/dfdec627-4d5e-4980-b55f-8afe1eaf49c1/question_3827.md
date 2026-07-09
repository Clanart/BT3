# Q3827: NEAR near_withdraw_callback cleanup order around callbacks reopens or strands value

## Question
Can an unprivileged attacker trigger `callback after unwrapping wNEAR during public payouts` so that `near/omni-bridge/src/lib.rs::near_withdraw_callback` cleans up transfer or fast-transfer state in an order that either reopens replay or strands user funds after callback failure, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Focus on removal of pending records, finalization flags, and lock rollback relative to payout callbacks.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Inject failures at each callback boundary and assert that cleanup always leaves one consistent recoverable state.
