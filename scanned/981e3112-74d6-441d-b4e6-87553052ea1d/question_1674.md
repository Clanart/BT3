# Q1674: NEAR deploy_token entry decimal cap creates wrong economic model through cross-module drift

## Question
Can an unprivileged attacker use `public `deploy_token` proof-submission flow` with control over proof bytes, source chain selection, attached deposit, and timing relative to other token deployments and desynchronize `near/omni-bridge/src/lib.rs::deploy_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `decimal cap creates wrong economic model` attack class because verifies a metadata proof and forwards the attached deposit into `deploy_token_callback` to deploy a wrapped or native bridge token on Near, violating `one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token`
- Entrypoint: `public `deploy_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing relative to other token deployments
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: one remote asset and one metadata event must map to one canonical token deployment with coherent decimals and storage accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token` and the adjacent token-mapping and asset-identity logic after every branch.
