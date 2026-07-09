# Q3781: Solana log_metadata ABI version switch changes metadata identity

## Question
Can an unprivileged attacker trigger `public Solana `log_metadata` instruction` so that `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` chooses the wrong ABI branch for metadata parsing because of reads Solana token metadata and posts it back toward Near for cross-chain deployment flows, violating `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target old-style versus new-style token metadata return shapes and zero-length special cases.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous ABI payloads and assert that the bridge either rejects them or derives the exact intended metadata once.
