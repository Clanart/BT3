# Q426: Starknet deploy_token canonical token identity collision through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet deploy-token entrypoint` with control over signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash and desynchronize `starknet/src/omni_bridge.cairo::deploy_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `canonical token identity collision` attack class because checks pause flags, verifies a Borsh payload signature, hashes the token id, computes a deploy salt, deploys a bridge token, and writes bidirectional mappings, violating `one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token`
- Entrypoint: `public Starknet deploy-token entrypoint`
- Attacker controls: signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::deploy_token` and the adjacent token-mapping and asset-identity logic after every branch.
