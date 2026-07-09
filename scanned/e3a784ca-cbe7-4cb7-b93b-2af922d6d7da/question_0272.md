# Q272: NEAR omni-types Starknet events proof kind or event class confusion via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet proof and deployment mapping flows` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-types/src/starknet/events.rs` ends up accepting two inconsistent interpretations of the same economic event specifically around `proof kind or event class confusion` under defines Starknet-side event structures consumed by the broader bridge type system, violating `Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model`?

## Target
- File/function: `near/omni-types/src/starknet/events.rs`
- Entrypoint: `public Starknet proof and deployment mapping flows`
- Attacker controls: Starknet event bytes, field ordering, and cross-chain address/string conversions
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: Starknet event parsing must stay domain-separated so event fields cannot collide with another chain’s asset or recipient model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
