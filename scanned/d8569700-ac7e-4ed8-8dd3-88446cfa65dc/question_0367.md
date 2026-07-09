# Q367: NEAR remove_fast_transfer removed fast transfer can be replayed or claimed through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reached from public callbacks and fee claims` with control over fast-transfer id, storage owner, and timing relative to claim or refund and desynchronize `near/omni-bridge/src/lib.rs::remove_fast_transfer` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `removed fast transfer can be replayed or claimed` attack class because removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner, violating `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Look for paths that remove state on refund or fee claim while another leg still depends on it for replay protection or storage refund. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force early removal and assert that no subsequent proof, claim, or callback can recreate or profit from the same fast-transfer id. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::remove_fast_transfer` and the adjacent storage billing and refund bookkeeping after every branch.
