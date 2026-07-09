# Q1259: Starknet deploy_token decimal cap creates wrong economic model at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet deploy-token entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::deploy_token` violate `one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model` in the `decimal cap creates wrong economic model` attack class because checks pause flags, verifies a Borsh payload signature, hashes the token id, computes a deploy salt, deploys a bridge token, and writes bidirectional mappings becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token`
- Entrypoint: `public Starknet deploy-token entrypoint`
- Attacker controls: signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
