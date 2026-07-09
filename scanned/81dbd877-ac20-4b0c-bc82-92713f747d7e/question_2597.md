# Q2597: NEAR fast_fin_transfer_to_near callback fast amount-plus-fee check can be bypassed

## Question
Can an unprivileged attacker craft a fast-transfer input to `callback after fast path storage checks for a Near recipient` so that `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback` accepts an amount-plus-fee equality that does not correspond to the eventual canonical transfer because of adds fast-transfer state, updates storage balances, emits a fast-transfer event, sends tokens immediately to the Near recipient, and later resolves the transfer via callback, violating `the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback`
- Entrypoint: `callback after fast path storage checks for a Near recipient`
- Attacker controls: storage-check result, relayer id, storage payer, fast transfer contents, `msg`, and token id
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths.
- Invariant to test: the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token.
