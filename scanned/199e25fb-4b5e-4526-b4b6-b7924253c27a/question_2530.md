# Q2530: NEAR omni-types Starknet events shared proof response reused across entrypoints at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet proof and deployment mapping flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/starknet/events.rs` violate `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model` in the `shared proof response reused across entrypoints` attack class because defines Starknet-side event structures consumed by the broader bridge type system becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
