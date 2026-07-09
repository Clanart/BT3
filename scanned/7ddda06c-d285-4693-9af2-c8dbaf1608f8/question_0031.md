# Q31: NEAR remove_fast_transfer removed fast transfer can be replayed or claimed

## Question
Can an unprivileged attacker use `internal helper reached from public callbacks and fee claims` to force `near/omni-bridge/src/lib.rs::remove_fast_transfer` to remove fast-transfer state before every dependent effect is final, violating `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Look for paths that remove state on refund or fee claim while another leg still depends on it for replay protection or storage refund.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force early removal and assert that no subsequent proof, claim, or callback can recreate or profit from the same fast-transfer id.
