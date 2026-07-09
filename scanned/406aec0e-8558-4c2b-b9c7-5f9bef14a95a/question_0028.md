# Q28: NEAR add_fin_transfer replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `internal finalization-state writer reached from public finalize flows` and make `near/omni-bridge/src/lib.rs::add_fin_transfer` either bypass replay protection or consume it for the wrong event because of inserts a transfer id into `finalised_transfers` and charges storage for that finalized record, violating `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
