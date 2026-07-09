# Q648: NEAR deploy_token_internal canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token_internal` violate `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails` in the `canonical token identity collision` attack class because registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
