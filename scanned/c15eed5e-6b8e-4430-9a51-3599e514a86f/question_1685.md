# Q1685: NEAR get_next_destination_nonce one inbound event spawns multiple outbound obligations through cross-module drift

## Question
Can an unprivileged attacker use `internal nonce allocator reached from public init and finalize flows` with control over destination chain choice, call ordering across chained settlement flows, and repeated retries and desynchronize `near/omni-bridge/src/lib.rs::get_next_destination_nonce` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `one inbound event spawns multiple outbound obligations` attack class because returns zero for Near destinations and monotonically increments a per-chain nonce map for all other chains, violating `destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_next_destination_nonce`
- Entrypoint: `internal nonce allocator reached from public init and finalize flows`
- Attacker controls: destination chain choice, call ordering across chained settlement flows, and repeated retries
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_next_destination_nonce` and the adjacent replay-protection bookkeeping after every branch.
