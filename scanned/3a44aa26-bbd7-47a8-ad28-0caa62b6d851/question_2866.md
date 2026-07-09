# Q2866: NEAR near_withdraw_callback callback-bearing token flow exposes inconsistent intermediate state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after unwrapping wNEAR during public payouts` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::near_withdraw_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback-bearing token flow exposes inconsistent intermediate state` under sends raw NEAR to the recipient only after the wrapped-token withdrawal promise succeeds, violating `unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::near_withdraw_callback`
- Entrypoint: `callback after unwrapping wNEAR during public payouts`
- Attacker controls: recipient account, amount, and success/failure of the preceding wNEAR withdrawal
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: unwrap callbacks must not create a state where finalization succeeded but the native payout can be replayed, redirected, or permanently stranded
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
