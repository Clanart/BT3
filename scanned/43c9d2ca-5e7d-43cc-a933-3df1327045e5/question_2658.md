# Q2658: Solana finalize_transfer stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `public Solana `finalize_transfer` instruction` and make `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer` treat it as fresh because of verifies the NEAR-derived signature, uses `UsedNonces::use_nonce`, then either transfers from the native vault or mints bridged supply and posts a completion message back to Near, violating `a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer`
- Entrypoint: `public Solana `finalize_transfer` instruction`
- Attacker controls: signed payload bytes, destination nonce, mint account, recipient account, vault existence, payer funding, and account ordering
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
