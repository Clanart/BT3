# Q1093: Starknet deploy_token decimal cap creates wrong economic model through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet deploy-token entrypoint` with control over signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash and desynchronize `starknet/src/omni_bridge.cairo::deploy_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `decimal cap creates wrong economic model` attack class because checks pause flags, verifies a Borsh payload signature, hashes the token id, computes a deploy salt, deploys a bridge token, and writes bidirectional mappings, violating `one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token`
- Entrypoint: `public Starknet deploy-token entrypoint`
- Attacker controls: signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::deploy_token` and the adjacent token-mapping and asset-identity logic after every branch.
