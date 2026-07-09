# Q1454: Starknet token-id hash mapping native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public Starknet `deploy_token`` and make `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` treat a wrapped asset as native or a native asset as wrapped because of hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address, violating `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
