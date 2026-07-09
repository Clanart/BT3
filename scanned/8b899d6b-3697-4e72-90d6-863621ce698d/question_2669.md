# Q2669: Starknet deploy_token hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public Starknet deploy-token entrypoint` with overlong or adversarial token identifiers and make `starknet/src/omni_bridge.cairo::deploy_token` derive the same local seed or salt for two remote assets because of checks pause flags, verifies a Borsh payload signature, hashes the token id, computes a deploy salt, deploys a bridge token, and writes bidirectional mappings, violating `one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token`
- Entrypoint: `public Starknet deploy-token entrypoint`
- Attacker controls: signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
