# Q3145: Solana metadata-seed derivation truncated seed or salt aliases remote assets at boundary values

## Question
Can an unprivileged attacker trigger `public `deploy_token`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs metadata PDA creation` violate `seed derivation must not let long token ids, collisions, or PDA reuse create a second remote asset that aliases an existing mint or metadata account` in the `truncated seed or salt aliases remote assets` attack class because creates metadata for a newly deployed wrapped mint using a hashed-or-padded token string as the core remote identity becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs metadata PDA creation`
- Entrypoint: `public `deploy_token``
- Attacker controls: mint PDA, metadata PDA seeds, payer funding, and extremely long token strings that get hashed before seed use
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: seed derivation must not let long token ids, collisions, or PDA reuse create a second remote asset that aliases an existing mint or metadata account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
