# Q754: Solana deploy_token decimal cap creates wrong economic model

## Question
Can an unprivileged attacker reach `public Solana `deploy_token` instruction` with a token whose remote decimals exceed local limits and make `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` register a representation that breaks backing assumptions because of verifies a NEAR-derived signature, creates a mint PDA from a hashed token string, writes metadata, and posts the new mint registration back to Near, violating `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset.
