# Q1352: NEAR deploy_token entry decimal cap creates wrong economic model

## Question
Can an unprivileged attacker reach `public `deploy_token` proof-submission flow` with a token whose remote decimals exceed local limits and make `near/omni-bridge/src/lib.rs::deploy_token` register a representation that breaks backing assumptions because of verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near, violating `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset.
