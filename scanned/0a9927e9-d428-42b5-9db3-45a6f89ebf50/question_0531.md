# Q531: NEAR get_next_destination_nonce origin and destination nonce desynchronization at boundary values

## Question
Can an unprivileged attacker trigger `internal nonce allocator reached from public init and finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::get_next_destination_nonce` violate `destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow` in the `origin and destination nonce desynchronization` attack class because returns zero for Near destinations and monotonically increments a per-chain nonce map for all other chains becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_next_destination_nonce`
- Entrypoint: `internal nonce allocator reached from public init and finalize flows`
- Attacker controls: destination chain choice, call ordering across chained settlement flows, and repeated retries
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
