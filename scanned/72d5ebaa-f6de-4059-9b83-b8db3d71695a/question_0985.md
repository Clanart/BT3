# Q985: NEAR near_withdraw_callback delivery callback leaves inconsistent state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after unwrapping wNEAR during public payouts` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::near_withdraw_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `delivery callback leaves inconsistent state` under sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
