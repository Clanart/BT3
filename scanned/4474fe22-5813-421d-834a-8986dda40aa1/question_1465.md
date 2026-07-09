# Q1465: Solana SOL vault branch one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public Solana native-SOL bridge instructions` and make `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs` both release local value and create a second valid outbound bridge obligation via moves SOL through a dedicated vault PDA and signs/consumes payloads that are similar to token-based flows but with different account models, violating `native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs`
- Entrypoint: `public Solana native-SOL bridge instructions`
- Attacker controls: payer lamports, vault balances, signed payload, destination nonce, and fee fields
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
