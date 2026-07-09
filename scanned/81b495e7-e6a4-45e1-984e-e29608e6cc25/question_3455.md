# Q3455: NEAR fast_fin_transfer_to_near callback removed fast transfer can be replayed or claimed through cross-module drift

## Question
Can an unprivileged attacker use `callback after fast path storage checks for a Near recipient` with control over storage-check result, relayer id, storage payer, fast transfer contents, `msg`, and token id and desynchronize `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `removed fast transfer can be replayed or claimed` attack class because adds fast-transfer state, updates storage balances, emits a fast-transfer event, sends tokens immediately to the Near recipient, and later resolves the transfer via callback, violating `the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback`
- Entrypoint: `callback after fast path storage checks for a Near recipient`
- Attacker controls: storage-check result, relayer id, storage payer, fast transfer contents, `msg`, and token id
- Exploit idea: Look for paths that remove state on refund or fee claim while another leg still depends on it for replay protection or storage refund. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: the fast-transfer record, recipient payout, and later burn/remove logic must stay symmetrical so immediate delivery cannot create unbacked or reclaimable supply
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force early removal and assert that no subsequent proof, claim, or callback can recreate or profit from the same fast-transfer id. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fast_fin_transfer_to_near_callback` and the adjacent storage billing and refund bookkeeping after every branch.
