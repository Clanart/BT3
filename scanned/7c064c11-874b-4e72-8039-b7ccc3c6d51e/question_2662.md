# Q2662: Solana deploy_token fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `public Solana `deploy_token` instruction` to register or settle against a token that only looks bridge-controlled because `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` relies on verifies a NEAR-derived signature, creates a mint PDA from a hashed token string, writes metadata, and posts the new mint registration back to Near, violating `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
