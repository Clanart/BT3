# Q1918: NEAR omni-types Starknet events optional-field encoding ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet proof and deployment mapping flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/starknet/events.rs` violate `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model` in the `optional-field encoding ambiguity` attack class because defines Starknet-side event structures consumed by the broader bridge type system becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
