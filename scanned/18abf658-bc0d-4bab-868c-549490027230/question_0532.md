# Q532: NEAR add_fin_transfer replay guard can be bypassed or consumed incorrectly at boundary values

## Question
Can an unprivileged attacker trigger `internal finalization-state writer reached from public finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fin_transfer` violate `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event` in the `replay guard can be bypassed or consumed incorrectly` attack class because inserts a transfer id into `finalised_transfers` and charges storage for that finalized record becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
