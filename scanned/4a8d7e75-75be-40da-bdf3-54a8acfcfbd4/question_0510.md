# Q510: NEAR fin_transfer entry replay guard can be bypassed or consumed incorrectly at boundary values

## Question
Can an unprivileged attacker trigger `public `fin_transfer` proof-submission flow` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::fin_transfer` violate `one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized` in the `replay guard can be bypassed or consumed incorrectly` attack class because verifies a proof through the configured prover, optionally prepays storage for recipient and fee accounts, then dispatches to `fin_transfer_callback` becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer`
- Entrypoint: `public `fin_transfer` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
