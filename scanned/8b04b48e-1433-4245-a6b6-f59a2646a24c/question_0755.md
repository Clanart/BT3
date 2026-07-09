# Q755: Solana log_metadata native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public Solana `log_metadata` instruction` and make `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` treat a wrapped asset as native or a native asset as wrapped because of reads Solana token metadata and posts it back toward Near for cross-chain deployment flows, violating `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
