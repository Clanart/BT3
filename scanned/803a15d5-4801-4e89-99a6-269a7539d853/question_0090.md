# Q90: Starknet deploy_token canonical token identity collision

## Question
Can an unprivileged attacker reach `public Starknet deploy-token entrypoint` with a valid-looking remote asset identity and make `starknet/src/omni_bridge.cairo::deploy_token` map it onto an existing local token because of checks pause flags, verifies a Borsh payload signature, hashes the token id, computes a deploy salt, deploys a bridge token, and writes bidirectional mappings, violating `one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token`
- Entrypoint: `public Starknet deploy-token entrypoint`
- Attacker controls: signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps.
- Invariant to test: one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row.
