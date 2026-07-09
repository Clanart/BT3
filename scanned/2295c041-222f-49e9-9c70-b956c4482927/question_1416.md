# Q1416: Solana log_metadata fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `public Solana `log_metadata` instruction` to register or settle against a token that only looks bridge-controlled because `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` relies on reads Solana token metadata and posts it back toward Near for cross-chain deployment flows, violating `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
