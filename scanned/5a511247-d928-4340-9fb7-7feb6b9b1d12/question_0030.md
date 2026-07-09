# Q30: NEAR mark_fast_transfer_as_finalised fast-transfer status changes in the wrong order

## Question
Can an unprivileged attacker trigger `internal helper reached from public fast/finalize flows` so that `near/omni-bridge/src/lib.rs::mark_fast_transfer_as_finalised` marks, removes, or reuses fast-transfer state in an order that opens replay or fee-claim gaps, violating `finalisation markers must transition exactly once and only after the matching economic leg has become irreversible`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::mark_fast_transfer_as_finalised`
- Entrypoint: `internal helper reached from public fast/finalize flows`
- Attacker controls: fast-transfer id and timing relative to fee claim and second-leg settlement
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs.
- Invariant to test: finalisation markers must transition exactly once and only after the matching economic leg has become irreversible
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle.
