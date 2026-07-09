# Q3794: NEAR omni-types Starknet events hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public Starknet proof and deployment mapping flows` with overlong or adversarial token identifiers and make `near/omni-types/src/starknet/events.rs` derive the same local seed or salt for two remote assets because of defines Starknet-side event structures consumed by the broader bridge type system, violating `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model`?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
