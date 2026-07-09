# Q868: NEAR remove_fast_transfer fast-transfer storage refund reaches wrong party via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public callbacks and fee claims` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::remove_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast-transfer storage refund reaches wrong party` under removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner, violating `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
