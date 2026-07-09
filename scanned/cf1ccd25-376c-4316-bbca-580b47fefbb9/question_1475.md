# Q1475: NEAR deploy_token_internal decimal cap creates wrong economic model

## Question
Can an unprivileged attacker reach `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback` with a token whose remote decimals exceed local limits and make `near/omni-bridge/src/lib.rs::deploy_token_internal` register a representation that breaks backing assumptions because of registers token mappings and either deploys a fresh token through a deployer or binds a native token representation for the chain, violating `internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_internal`
- Entrypoint: `public deploy flow reached from `deploy_token`, `deploy_native_token`, and token-deployer callback`
- Attacker controls: chain kind, foreign token address, metadata, attached deposit, and whether a chain-specific token deployer exists
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows.
- Invariant to test: internal deployment must preserve one canonical Near token id per foreign asset and must roll back cleanly if any downstream deployment step fails
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset.
