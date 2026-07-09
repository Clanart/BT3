# Q3244: Solana log_metadata truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public Solana `log_metadata` instruction` and make `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
