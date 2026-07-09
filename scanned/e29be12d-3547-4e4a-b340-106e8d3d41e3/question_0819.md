# Q819: NEAR near_withdraw_callback delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `callback after unwrapping wNEAR during public payouts` that causes `near/omni-bridge/src/lib.rs::near_withdraw_callback` to keep or remove settlement state inconsistently with delivered value because of sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
