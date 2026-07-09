# Q805: Solana SOL vault branch replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public Solana native-SOL bridge instructions` and make `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs` either bypass replay protection or consume it for the wrong event because of moves SOL through a dedicated vault PDA and signs/consumes payloads that are similar to token-based flows but with different account models, violating `native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs`
- Entrypoint: `public Solana native-SOL bridge instructions`
- Attacker controls: payer lamports, vault balances, signed payload, destination nonce, and fee fields
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
