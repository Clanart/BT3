# Q2463: NEAR get_next_destination_nonce bitmap slot boundary corrupts replay protection at boundary values

## Question
Can an unprivileged attacker trigger `internal nonce allocator reached from public init and finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::get_next_destination_nonce` violate `destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow` in the `bitmap slot boundary corrupts replay protection` attack class because returns zero for Near destinations and monotonically increments a per-chain nonce map for all other chains becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_next_destination_nonce`
- Entrypoint: `internal nonce allocator reached from public init and finalize flows`
- Attacker controls: destination chain choice, call ordering across chained settlement flows, and repeated retries
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
