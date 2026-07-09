# Q3663: NEAR omni-types Starknet events same remote asset deployable via multiple proof paths at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet proof and deployment mapping flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/starknet/events.rs` violate `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model` in the `same remote asset deployable via multiple proof paths` attack class because defines Starknet-side event structures consumed by the broader bridge type system becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
