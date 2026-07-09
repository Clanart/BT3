# Q2162: NEAR remove_fast_transfer storage withdrawal escapes live liabilities via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public callbacks and fee claims` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::remove_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage withdrawal escapes live liabilities` under removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner, violating `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
