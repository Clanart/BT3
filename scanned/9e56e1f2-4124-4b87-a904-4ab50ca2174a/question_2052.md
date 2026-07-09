# Q2052: Solana finalize_transfer emitter or factory binding mismatch

## Question
Can an unprivileged attacker submit a structurally valid proof to `public Solana `finalize_transfer` instruction` whose payload points to one source chain while `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer` authenticates another because of verifies the NEAR-derived signature, uses `UsedNonces::use_nonce`, then either transfers from the native vault or mints bridged supply and posts a completion message back to Near, violating `a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer`
- Entrypoint: `public Solana `finalize_transfer` instruction`
- Attacker controls: signed payload bytes, destination nonce, mint account, recipient account, vault existence, payer funding, and account ordering
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps.
- Invariant to test: a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees.
