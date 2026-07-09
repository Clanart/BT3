# Q27: NEAR get_next_destination_nonce origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `internal nonce allocator reached from public init and finalize flows` with control over destination chain choice, call ordering across chained settlement flows, and repeated retries and make `near/omni-bridge/src/lib.rs::get_next_destination_nonce` advance or reuse bridge nonces inconsistently with returns zero for Near destinations and monotonically increments a per-chain nonce map for all other chains, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_next_destination_nonce`
- Entrypoint: `internal nonce allocator reached from public init and finalize flows`
- Attacker controls: destination chain choice, call ordering across chained settlement flows, and repeated retries
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: destination nonces must stay unique for every externally-consumable destination message regardless of branch, retry, or recursive bridge flow
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
