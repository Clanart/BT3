# Q3058: NEAR remove_fast_transfer refund goes to wrong logical owner at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public callbacks and fee claims` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::remove_fast_transfer` violate `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting` in the `refund goes to wrong logical owner` attack class because removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
