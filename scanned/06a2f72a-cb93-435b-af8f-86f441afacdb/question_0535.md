# Q535: NEAR remove_fast_transfer removed fast transfer can be replayed or claimed at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public callbacks and fee claims` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::remove_fast_transfer` violate `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting` in the `removed fast transfer can be replayed or claimed` attack class because removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Look for paths that remove state on refund or fee claim while another leg still depends on it for replay protection or storage refund. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force early removal and assert that no subsequent proof, claim, or callback can recreate or profit from the same fast-transfer id. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
