# Q440: NEAR omni-types Starknet events proof kind or event class confusion through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet proof and deployment mapping flows` with control over Starknet event bytes, field ordering, and cross-chain address/string conversions and desynchronize `near/omni-types/src/starknet/events.rs` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `proof kind or event class confusion` attack class because defines Starknet-side event structures consumed by the broader bridge type system, violating `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model`?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Also assert cross-module consistency between `near/omni-types/src/starknet/events.rs` and the adjacent proof parsing and source authentication after every branch.
