# Q794: Starknet token-id hash mapping decimal cap creates wrong economic model

## Question
Can an unprivileged attacker reach `public Starknet `deploy_token`` with a token whose remote decimals exceed local limits and make `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` register a representation that breaks backing assumptions because of hashes the Near token id, stores the full hash as the map key, but uses only the low part as deploy salt for the contract address, violating `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset.
