# Q534: NEAR mark_fast_transfer_as_finalised fast-transfer status changes in the wrong order at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public fast/finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::mark_fast_transfer_as_finalised` violate `finalisation markers must transition exactly once and only after the matching economic leg has become irreversible` in the `fast-transfer status changes in the wrong order` attack class because flips a stored fast-transfer status from pending to finalised without changing any other fields becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::mark_fast_transfer_as_finalised`
- Entrypoint: `internal helper reached from public fast/finalize flows`
- Attacker controls: fast-transfer id and timing relative to fee claim and second-leg settlement
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: finalisation markers must transition exactly once and only after the matching economic leg has become irreversible
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
