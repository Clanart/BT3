# Q2568: NEAR deploy_token_internal malicious metadata manufactures a bridge identity at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token_internal` violate `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails` in the `malicious metadata manufactures a bridge identity` attack class because registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
