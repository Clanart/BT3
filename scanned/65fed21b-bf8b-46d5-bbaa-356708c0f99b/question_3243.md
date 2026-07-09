# Q3243: Solana deploy_token remote publication drifts from local deployment state

## Question
Can an unprivileged attacker exploit `public Solana `deploy_token` instruction` so that `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` publishes a deploy or metadata message that no longer matches local token state because of verifies a NEAR-derived signature, creates a mint PDA from a hashed token string, writes metadata, and posts the new mint registration back to Near, violating `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token.
