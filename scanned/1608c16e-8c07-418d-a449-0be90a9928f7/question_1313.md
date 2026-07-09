# Q1313: NEAR deploy_token_internal partial deployment rollback leaves live alias at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token_internal` violate `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails` in the `partial deployment rollback leaves live alias` attack class because registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
