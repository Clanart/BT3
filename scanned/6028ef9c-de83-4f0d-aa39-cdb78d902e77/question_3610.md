# Q3610: NEAR remove_fast_transfer unregister can sever state that callbacks still need at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public callbacks and fee claims` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::remove_fast_transfer` violate `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting` in the `unregister can sever state that callbacks still need` attack class because removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
