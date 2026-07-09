# Q865: NEAR get_next_destination_nonce replay guard can be bypassed or consumed incorrectly via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal nonce allocator reached from public init and finalize flows` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-bridge/src/lib.rs::get_next_destination_nonce` ends up accepting two inconsistent interpretations of the same economic event specifically around `replay guard can be bypassed or consumed incorrectly` under returns zero for Near destinations and monotonically increments a per-chain nonce map for all other chains, violating `destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_next_destination_nonce`
- Entrypoint: `internal nonce allocator reached from public init and finalize flows`
- Attacker controls: destination chain choice, call ordering across chained settlement flows, and repeated retries
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
