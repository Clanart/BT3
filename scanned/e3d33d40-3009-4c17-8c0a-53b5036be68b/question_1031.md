# Q1031: NEAR get_next_destination_nonce replay guard can be bypassed or consumed incorrectly through cross-module drift

## Question
Can an unprivileged attacker use `internal nonce allocator reached from public init and finalize flows` with control over destination chain choice, call ordering across chained settlement flows, and repeated retries and desynchronize `near/omni-bridge/src/lib.rs::get_next_destination_nonce` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay guard can be bypassed or consumed incorrectly` attack class because returns zero for Near destinations and monotonically increments a per-chain nonce map for all other chains, violating `destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_next_destination_nonce`
- Entrypoint: `internal nonce allocator reached from public init and finalize flows`
- Attacker controls: destination chain choice, call ordering across chained settlement flows, and repeated retries
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_next_destination_nonce` and the adjacent replay-protection bookkeeping after every branch.
