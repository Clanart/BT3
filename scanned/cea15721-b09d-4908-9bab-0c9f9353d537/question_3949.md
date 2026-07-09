# Q3949: NEAR deploy_token_internal low-half deploy salt aliases another token id via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `low-half deploy salt aliases another token id` under registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
