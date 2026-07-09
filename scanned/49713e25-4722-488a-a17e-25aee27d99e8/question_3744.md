# Q3744: NEAR remove_fast_transfer promise bookkeeping can be overwritten or orphaned

## Question
Can an unprivileged attacker use `internal helper reached from public callbacks and fee claims` to overwrite or strand another deferred operation inside `near/omni-bridge/src/lib.rs::remove_fast_transfer` because of removes stored fast-transfer state and refunds reserved storage balance to the recorded storage owner, violating `removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_fast_transfer`
- Entrypoint: `internal helper reached from public callbacks and fee claims`
- Attacker controls: fast-transfer id, storage owner, and timing relative to claim or refund
- Exploit idea: Probe maps keyed only by account ids or derived storage accounts when multiple pending operations are possible.
- Invariant to test: removal must not refund the wrong payer or reopen a fast transfer that can still influence claim, payout, or lock accounting
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Create overlapping deferred operations and assert that each one has independent bookkeeping and cleanup.
