# Q366: NEAR mark_fast_transfer_as_finalised fast-transfer status changes in the wrong order through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reached from public fast/finalize flows` with control over fast-transfer id and timing relative to fee claim and second-leg settlement and desynchronize `near/omni-bridge/src/lib.rs::mark_fast_transfer_as_finalised` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast-transfer status changes in the wrong order` attack class because flips a stored fast-transfer status from pending to finalised without changing any other fields, violating `finalisation markers must transition exactly once and only after the matching economic leg has become irreversible`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::mark_fast_transfer_as_finalised`
- Entrypoint: `internal helper reached from public fast/finalize flows`
- Attacker controls: fast-transfer id and timing relative to fee claim and second-leg settlement
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: finalisation markers must transition exactly once and only after the matching economic leg has become irreversible
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::mark_fast_transfer_as_finalised` and the adjacent replay-protection bookkeeping after every branch.
