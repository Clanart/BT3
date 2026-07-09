# Q1345: NEAR fast_fin_transfer_to_near callback fast path changes fee semantics without changing proof identity

## Question
Can an unprivileged attacker use `callback after fast path storage checks for a Near recipient` to create a fast-transfer state whose effective fee differs from the fee later proven and claimed via `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback`, violating `the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback`
- Entrypoint: `callback after fast path storage checks for a Near recipient`
- Attacker controls: storage-check result, relayer id, storage payer, fast transfer contents, `msg`, and token id
- Exploit idea: Target relayer-sponsored fast paths where the first leg is paid before the canonical proof arrives.
- Invariant to test: the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare claimed fee, relayer payout, and stored transfer fee across both legs and assert that the bridge never accepts a mismatch.
