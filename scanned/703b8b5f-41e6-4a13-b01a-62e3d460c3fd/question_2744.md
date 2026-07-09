# Q2744: NEAR fast_fin_transfer_to_near callback fast amount-plus-fee check can be bypassed via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after fast path storage checks for a Near recipient` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast amount-plus-fee check can be bypassed` under adds fast-transfer state, updates storage balances, emits a fast-transfer event, sends tokens immediately to the Near recipient, and later resolves the transfer via callback, violating `the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback`
- Entrypoint: `callback after fast path storage checks for a Near recipient`
- Attacker controls: storage-check result, relayer id, storage payer, fast transfer contents, `msg`, and token id
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
