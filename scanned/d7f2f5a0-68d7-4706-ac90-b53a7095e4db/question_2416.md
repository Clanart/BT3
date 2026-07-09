# Q2416: NEAR deploy_token_internal malicious metadata manufactures a bridge identity through cross-module drift

## Question
Can an unprivileged attacker use `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` with control over chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_internal` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `malicious metadata manufactures a bridge identity` attack class because registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_internal` and the adjacent token-mapping and asset-identity logic after every branch.
