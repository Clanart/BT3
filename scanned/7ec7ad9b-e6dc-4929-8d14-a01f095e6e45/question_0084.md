# Q84: Solana log_metadata malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public Solana `log_metadata` instruction` with a malicious token or metadata payload so that `solana/programs/bridge_token_factory/src/lib.rs::log_metadata` records a deceptive asset identity that later drives deployment or claims, violating `metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::log_metadata`
- Entrypoint: `public Solana `log_metadata` instruction`
- Attacker controls: mint account, metadata account contents, payer funding, and published payload fields
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: metadata publication must not let a malicious mint or metadata account manufacture a remote asset identity that diverges from actual custody semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
