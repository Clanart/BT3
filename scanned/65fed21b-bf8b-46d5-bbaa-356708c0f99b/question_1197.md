# Q1197: NEAR get_next_destination_nonce replay guard can be bypassed or consumed incorrectly at boundary values

## Question
Can an unprivileged attacker trigger `internal nonce allocator reached from public init and finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::get_next_destination_nonce` violate `destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow` in the `replay guard can be bypassed or consumed incorrectly` attack class because returns zero for Near destinations and monotonically increments a per-chain nonce map for all other chains becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_next_destination_nonce`
- Entrypoint: `internal nonce allocator reached from public init and finalize flows`
- Attacker controls: destination chain choice, call ordering across chained settlement flows, and repeated retries
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
