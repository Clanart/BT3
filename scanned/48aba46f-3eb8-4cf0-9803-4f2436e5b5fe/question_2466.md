# Q2466: NEAR remove_fast_transfer storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public callbacks and fee claims` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::remove_fast_transfer` violate `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting` in the `storage withdrawal escapes live liabilities` attack class because removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
