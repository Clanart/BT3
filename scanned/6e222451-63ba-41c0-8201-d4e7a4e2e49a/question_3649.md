# Q3649: Solana log_metadata truncated seed or salt aliases remote assets at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `log_metadata` instruction` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` violate `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics` in the `truncated seed or salt aliases remote assets` attack class because reads Solana token metadata and posts it back toward Near for cross-chain deployment flows becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
