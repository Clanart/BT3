# Q130: Solana metadata-seed derivation canonical token identity collision

## Question
Can an unprivileged attacker reach `public `deploy_token`` with a valid-looking remote asset identity and make `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs metadata PDA creation` map it onto an existing local token because of creates metadata for a newly deployed wrapped mint using a hashed-or-padded token string as the core remote identity, violating `seed derivation must not let long token ids, collisions, or PDA reuse create a second remote asset that aliases an existing mint or metadata account`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs metadata PDA creation`
- Entrypoint: `public `deploy_token``
- Attacker controls: mint PDA, metadata PDA seeds, payer funding, and extremely long token strings that get hashed before seed use
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps.
- Invariant to test: seed derivation must not let long token ids, collisions, or PDA reuse create a second remote asset that aliases an existing mint or metadata account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row.
