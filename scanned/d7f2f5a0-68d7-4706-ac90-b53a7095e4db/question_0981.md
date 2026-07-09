# Q981: NEAR deploy_token_internal partial deployment rollback leaves live alias via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `partial deployment rollback leaves live alias` under registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
