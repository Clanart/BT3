# Q1937: Starknet token-id hash mapping native versus wrapped registration confusion at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` violate `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses` in the `native versus wrapped registration confusion` attack class because hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
