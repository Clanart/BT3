# Q702: NEAR remove_fast_transfer fast-transfer storage refund reaches wrong party

## Question
Can an unprivileged attacker exploit `internal helper reached from public callbacks and fee claims` so that `near/omni-bridge/src/lib.rs::remove_fast_transfer` refunds reserved fast-transfer storage to the wrong account because of removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner, violating `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot.
