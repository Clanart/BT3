# Q3198: `get_current_l2_block_height` and replay of a withdrawal intent across rounds

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction resubmit the same withdrawal intent (same index, same signature, same UTXO) to `get_current_l2_block_height` in `core/src/citrea.rs` after a round boundary, an entity restart, or a database transaction rollback, so the intent is served twice and two vaults are spent for one burned cBTC amount?

## Target
- File/function: `core/src/citrea.rs` -> `get_current_l2_block_height`
- Entrypoint: aggregator `Withdraw` submitted repeatedly -> `get_current_l2_block_height`
- Attacker controls: request repetition, timing across round boundaries, and concurrent duplicate requests; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: duplicate a withdrawal intent across the protocol's idempotency boundaries
- Invariant to test: a given withdrawal index is served at most once across all rounds and restarts
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: submit the identical withdrawal twice concurrently and assert exactly one settlement occurs
