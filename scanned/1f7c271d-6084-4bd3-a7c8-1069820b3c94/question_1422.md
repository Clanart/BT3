# Q1422: Starknet deploy_token native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public Starknet deploy-token entrypoint` and make `starknet/src/omni_bridge.cairo::deploy_token` treat a wrapped asset as native or a native asset as wrapped because of checks pause flags, verifies a Borsh payload signature, hashes the token id, computes a deploy salt, deploys a bridge token, and writes bidirectional mappings, violating `one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token`
- Entrypoint: `public Starknet deploy-token entrypoint`
- Attacker controls: signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
