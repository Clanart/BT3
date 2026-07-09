# Q3138: Starknet token-id hash mapping hashed or padded seed collision at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` violate `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses` in the `hashed or padded seed collision` attack class because hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
