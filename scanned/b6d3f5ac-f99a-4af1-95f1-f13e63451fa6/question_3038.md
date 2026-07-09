# Q3038: NEAR fast_fin_transfer_to_near callback fast amount-plus-fee check can be bypassed at boundary values

## Question
Can an unprivileged attacker trigger `callback after fast path storage checks for a Near recipient` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback` violate `the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply` in the `fast amount-plus-fee check can be bypassed` attack class because adds fast-transfer state, updates storage balances, emits a fast-transfer event, sends tokens immediately to the Near recipient, and later resolves the transfer via callback becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback`
- Entrypoint: `callback after fast path storage checks for a Near recipient`
- Attacker controls: storage-check result, relayer id, storage payer, fast transfer contents, `msg`, and token id
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
