# Q3660: `create_ready_to_reimburse_txhandler` and the `is_utxo_spent` time-of-check window

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction spend the registered withdrawal UTXO themselves in the window between the `is_utxo_spent` check reached from `create_ready_to_reimburse_txhandler` in `core/src/builder/transaction/operator_collateral.rs` and the moment the N-of-N or operator signature is produced, so the bridge signs a settlement whose input no longer exists while the withdrawal is recorded as served?

## Target
- File/function: `core/src/builder/transaction/operator_collateral.rs` -> `create_ready_to_reimburse_txhandler` (This module contains the logic for creating the `round_tx`, `ready_to_reimburse_tx`,)
- Entrypoint: aggregator `OptimisticPayout` then a self-spend broadcast by an unprivileged party paying only mining fees -> `create_ready_to_reimburse_txhandler`
- Attacker controls: the timing of the competing spend and its fee; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: obtain a signed vault spend that can never confirm, or a withdrawal marked settled with no payout
- Invariant to test: the withdrawal UTXO is unspent at the instant the bridge commits to settling against it
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: spend the withdrawal UTXO mid-round and assert the settlement is abandoned cleanly
