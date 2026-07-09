# Q3648: Solana deploy_token remote publication drifts from local deployment state at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `deploy_token` instruction` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` violate `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch` in the `remote publication drifts from local deployment state` attack class because verifies a NEAR-derived signature, creates a mint PDA from a hashed token string, writes metadata, and posts the new mint registration back to Near becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
