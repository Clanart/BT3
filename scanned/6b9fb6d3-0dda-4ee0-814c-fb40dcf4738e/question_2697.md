# Q2697: Starknet token-id hash mapping hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public Starknet `deploy_token`` with overlong or adversarial token identifiers and make `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` derive the same local seed or salt for two remote assets because of hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address, violating `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
