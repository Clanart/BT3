# Q1252: Solana deploy_token decimal cap creates wrong economic model at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `deploy_token` instruction` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` violate `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch` in the `decimal cap creates wrong economic model` attack class because verifies a NEAR-derived signature, creates a mint PDA from a hashed token string, writes metadata, and posts the new mint registration back to Near becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
