# Q1740: Solana DeployToken initialize_token_metadata decimal cap creates wrong economic model through cross-module drift

## Question
Can an unprivileged attacker use `public deploy flow through `deploy_token`` with control over token string bytes, name, symbol, decimals, payer funding, and metadata PDA seeds and desynchronize `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `decimal cap creates wrong economic model` attack class because hashes overlong token strings to derive mint seeds, caps decimals, writes Metaplex metadata, and posts a `DeployTokenResponse` back to Near, violating `mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata`
- Entrypoint: `public deploy flow through `deploy_token``
- Attacker controls: token string bytes, name, symbol, decimals, payer funding, and metadata PDA seeds
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Also assert cross-module consistency between `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata` and the adjacent token-mapping and asset-identity logic after every branch.
