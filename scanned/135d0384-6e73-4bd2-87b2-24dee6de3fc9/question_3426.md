# Q3426: NEAR deploy_token_internal fake bridge-controlled token accepted as canonical via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `fake bridge-controlled token accepted as canonical` under registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
