# Q312: NEAR deploy_token_internal canonical token identity collision via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `canonical token identity collision` under registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
