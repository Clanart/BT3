# Q1899: Solana log_metadata fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `log_metadata` instruction` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` violate `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics` in the `fake bridge-controlled token accepted as canonical` attack class because reads Solana token metadata and posts it back toward Near for cross-chain deployment flows becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
