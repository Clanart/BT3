# Q2844: Starknet token-id hash mapping hashed or padded seed collision via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet `deploy_token`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` ends up accepting two inconsistent interpretations of the same economic event specifically around `hashed or padded seed collision` under hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address, violating `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
