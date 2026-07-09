# Q1787: Solana SOL vault branch one inbound event spawns multiple outbound obligations through cross-module drift

## Question
Can an unprivileged attacker use `public Solana native-SOL bridge instructions` with control over payer lamports, vault balances, signed payload, destination nonce, and fee fields and desynchronize `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `one inbound event spawns multiple outbound obligations` attack class because moves SOL through a dedicated vault PDA and signs/consumes payloads that are similar to token-based flows but with different account models, violating `native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs`
- Entrypoint: `public Solana native-SOL bridge instructions`
- Attacker controls: payer lamports, vault balances, signed payload, destination nonce, and fee fields
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Also assert cross-module consistency between `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs` and the adjacent replay-protection bookkeeping after every branch.
