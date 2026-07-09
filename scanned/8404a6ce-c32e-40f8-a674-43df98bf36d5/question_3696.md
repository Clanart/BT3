# Q3696: NEAR deploy_token_internal fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token_internal` violate `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails` in the `fake bridge-controlled token accepted as canonical` attack class because registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
