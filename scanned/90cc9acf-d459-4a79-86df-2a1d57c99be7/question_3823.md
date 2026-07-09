# Q3823: NEAR deploy_token_internal low-half deploy salt aliases another token id

## Question
Can an unprivileged attacker reach `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` and make `near/omni-bridge/src/lib.rs::deploy_token_internal` deploy or reference another token’s address because the contract address salt uses only part of a larger hash, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids.
