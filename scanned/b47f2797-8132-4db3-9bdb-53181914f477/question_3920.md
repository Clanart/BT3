# Q3920: NEAR omni-types Starknet events hashed or padded seed collision via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet proof and deployment mapping flows` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-types/src/starknet/events.rs` ends up accepting two inconsistent interpretations of the same economic event specifically around `hashed or padded seed collision` under defines Starknet-side event structures consumed by the broader bridge type system, violating `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model`?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
