# Q3258: NEAR omni-types Starknet events same remote asset deployable via multiple proof paths

## Question
Can an unprivileged attacker use `public Starknet proof and deployment mapping flows` to deploy or bind the same remote asset through a second path because `near/omni-types/src/starknet/events.rs` authenticates defines Starknet-side event structures consumed by the broader bridge type system differently than another deploy path, violating `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model`?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation.
