# Q1292: Starknet token-id hash mapping decimal cap creates wrong economic model at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` violate `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses` in the `decimal cap creates wrong economic model` attack class because hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
