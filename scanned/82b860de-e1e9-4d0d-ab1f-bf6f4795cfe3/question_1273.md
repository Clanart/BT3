# Q1273: NEAR omni-types Starknet events parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet proof and deployment mapping flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/starknet/events.rs` violate `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model` in the `parser boundary or offset manipulation` attack class because defines Starknet-side event structures consumed by the broader bridge type system becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
