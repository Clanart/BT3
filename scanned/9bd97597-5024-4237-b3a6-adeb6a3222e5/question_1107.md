# Q1107: NEAR omni-types Starknet events parser boundary or offset manipulation through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet proof and deployment mapping flows` with control over Starknet event bytes, field ordering, and cross-chain address/string conversions and desynchronize `near/omni-types/src/starknet/events.rs` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `parser boundary or offset manipulation` attack class because defines Starknet-side event structures consumed by the broader bridge type system, violating `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model`?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Also assert cross-module consistency between `near/omni-types/src/starknet/events.rs` and the adjacent proof parsing and source authentication after every branch.
