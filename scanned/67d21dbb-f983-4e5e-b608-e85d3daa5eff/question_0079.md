# Q79: Solana finalize_transfer replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public Solana `finalize_transfer` instruction` and make `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer` either bypass replay protection or consume it for the wrong event because of verifies the NEAR-derived signature, uses `UsedNonces::use_nonce`, then either transfers from the native vault or mints bridged supply and posts a completion message back to Near, violating `a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer`
- Entrypoint: `public Solana `finalize_transfer` instruction`
- Attacker controls: signed payload bytes, destination nonce, mint account, recipient account, vault existence, payer funding, and account ordering
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: a signed inbound transfer must settle exactly once to one mint/recipient pair and must not switch between native-vault and bridged-mint branches
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
