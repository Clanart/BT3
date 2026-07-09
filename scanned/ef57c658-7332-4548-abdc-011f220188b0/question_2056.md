# Q2056: Solana deploy_token native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public Solana `deploy_token` instruction` and make `solana/programs/bridge_token_factory/src/lib.rs::deploy_token` treat a wrapped asset as native or a native asset as wrapped because of verifies a NEAR-derived signature, creates a mint PDA from a hashed token string, writes metadata, and posts the new mint registration back to Near, violating `one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::deploy_token`
- Entrypoint: `public Solana `deploy_token` instruction`
- Attacker controls: signed payload bytes, token string, name, symbol, decimals, payer funding, and derived PDA seeds
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: one signed remote asset identity must map to one mint PDA and one metadata record with no collision or decimal mismatch
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
