# Q513: NEAR fast_fin_transfer_to_near callback native fee and token fee drawn from wrong asset bucket at boundary values

## Question
Can an unprivileged attacker trigger `callback after fast path storage checks for a Near recipient` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback` violate `the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply` in the `native fee and token fee drawn from wrong asset bucket` attack class because adds fast-transfer state, updates storage balances, emits a fast-transfer event, sends tokens immediately to the Near recipient, and later resolves the transfer via callback becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback`
- Entrypoint: `callback after fast path storage checks for a Near recipient`
- Attacker controls: storage-check result, relayer id, storage payer, fast transfer contents, `msg`, and token id
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
