# Q1757: NEAR omni-types Starknet events optional-field encoding ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet proof and deployment mapping flows` with control over Starknet event bytes, field ordering, and cross-chain address/string conversions and desynchronize `near/omni-types/src/starknet/events.rs` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `optional-field encoding ambiguity` attack class because defines Starknet-side event structures consumed by the broader bridge type system, violating `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model`?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Also assert cross-module consistency between `near/omni-types/src/starknet/events.rs` and the adjacent proof parsing and source authentication after every branch.
