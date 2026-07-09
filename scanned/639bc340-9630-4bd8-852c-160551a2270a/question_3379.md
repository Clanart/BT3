# Q3379: Solana log_metadata truncated seed or salt aliases remote assets via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana `log_metadata` instruction` and then replay or reorder later bind, deploy, or metadata-consumption step so that `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` ends up accepting two inconsistent interpretations of the same economic event specifically around `truncated seed or salt aliases remote assets` under reads Solana token metadata and posts it back toward Near for cross-chain deployment flows, violating `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
