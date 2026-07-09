# Q1411: Solana finalize_transfer delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `public Solana `finalize_transfer` instruction` that causes `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer` to keep or remove settlement state inconsistently with delivered value because of verifies the NEAR-derived signature, uses `UsedNonces::use_nonce`, then either transfers from the native vault or mints bridged supply and posts a completion message back to Near, violating `a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer`
- Entrypoint: `public Solana `finalize_transfer` instruction`
- Attacker controls: signed payload bytes, destination nonce, mint account, recipient account, vault existence, payer funding, and account ordering
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
