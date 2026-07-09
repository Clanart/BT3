# Q587: Solana deploy_token canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `deploy_token` instruction` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` violate `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch` in the `canonical token identity collision` attack class because verifies a NEAR-derived signature, creates a mint PDA from a hashed token string, writes metadata, and posts the new mint registration back to Near becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
