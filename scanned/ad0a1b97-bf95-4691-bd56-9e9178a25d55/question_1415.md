# Q1415: Solana deploy_token malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public Solana `deploy_token` instruction` with a malicious token or metadata payload so that `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` records a deceptive asset identity that later drives deployment or claims, violating `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
