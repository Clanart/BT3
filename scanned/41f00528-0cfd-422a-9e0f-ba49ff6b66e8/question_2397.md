# Q2397: Starknet token-id hash mapping fake bridge-controlled token accepted as canonical through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet `deploy_token`` with control over token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state and desynchronize `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `fake bridge-controlled token accepted as canonical` attack class because hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address, violating `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` and the adjacent token-mapping and asset-identity logic after every branch.
