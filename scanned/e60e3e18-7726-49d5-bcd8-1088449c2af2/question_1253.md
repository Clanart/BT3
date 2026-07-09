# Q1253: Solana log_metadata native versus wrapped registration confusion at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `log_metadata` instruction` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` violate `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics` in the `native versus wrapped registration confusion` attack class because reads Solana token metadata and posts it back toward Near for cross-chain deployment flows becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
