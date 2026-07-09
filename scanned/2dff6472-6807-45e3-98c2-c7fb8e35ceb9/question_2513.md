# Q2513: Solana log_metadata remote publication drifts from local deployment state at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `log_metadata` instruction` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` violate `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics` in the `remote publication drifts from local deployment state` attack class because reads Solana token metadata and posts it back toward Near for cross-chain deployment flows becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
