# Q2057: Solana log_metadata remote publication drifts from local deployment state

## Question
Can an unprivileged attacker exploit `public Solana `log_metadata` instruction` so that `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` publishes a deploy or metadata message that no longer matches local token state because of reads Solana token metadata and posts it back toward Near for cross-chain deployment flows, violating `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token.
