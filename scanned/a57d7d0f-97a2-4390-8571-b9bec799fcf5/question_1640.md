# Q1640: NEAR near_withdraw_callback mint-with-message path differs economically from plain mint via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after unwrapping wNEAR during public payouts` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::near_withdraw_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `mint-with-message path differs economically from plain mint` under sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
