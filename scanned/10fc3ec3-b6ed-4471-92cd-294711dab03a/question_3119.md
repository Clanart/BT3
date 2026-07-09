# Q3119: NEAR omni-types Starknet events address normalization changes authenticated subject at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet proof and deployment mapping flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/starknet/events.rs` violate `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model` in the `address normalization changes authenticated subject` attack class because defines Starknet-side event structures consumed by the broader bridge type system becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
