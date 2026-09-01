# Q4635: `create_payout_txhandler` and replay of a withdrawal intent across rounds

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction resubmit the same withdrawal intent (same index, same signature, same UTXO) to `create_payout_txhandler` in `core/src/builder/transaction/operator_reimburse.rs` after a round boundary, an entity restart, or a database transaction rollback, so the intent is served twice and two vaults are spent for one burned cBTC amount?

## Target
- File/function: `core/src/builder/transaction/operator_reimburse.rs` -> `create_payout_txhandler` (This module contains the logic for creating operator reimbursement and payout-related transactions in the protocol)
- Entrypoint: aggregator `Withdraw` submitted repeatedly -> `create_payout_txhandler`
- Attacker controls: request repetition, timing across round boundaries, and concurrent duplicate requests; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: duplicate a withdrawal intent across the protocol's idempotency boundaries
- Invariant to test: a given withdrawal index is served at most once across all rounds and restarts
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: submit the identical withdrawal twice concurrently and assert exactly one settlement occurs
