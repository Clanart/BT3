# Q2715: NEAR deploy_token_internal native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` and make `near/omni-bridge/src/lib.rs::deploy_token_internal` treat a wrapped asset as native or a native asset as wrapped because of registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
