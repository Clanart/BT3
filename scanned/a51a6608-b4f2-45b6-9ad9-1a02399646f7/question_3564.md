# Q3564: `into_task` uses one index for both the deposit and the withdrawal record

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction exploit that `into_task` in `core/src/task/tx_sender.rs` resolves both `get_move_to_vault_txid_from_citrea_deposit` and `get_withdrawal_utxo_from_citrea_withdrawal` with the same caller-supplied index, by registering a withdrawal at an index whose deposit record points at a vault the attacker did not fund, so a payout for a small withdrawal settles against a different deposit's vault?

## Target
- File/function: `core/src/task/tx_sender.rs` -> `into_task`
- Entrypoint: aggregator `Withdraw` / `OptimisticPayout` with a chosen index -> `into_task`
- Attacker controls: the index submitted in the request and the order in which withdrawals are registered on Citrea; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: pair a withdrawal with the wrong vault
- Invariant to test: the vault spent for withdrawal index i == the vault the Bridge contract's deposit record at index i names
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: assert the deposit and withdrawal indices are resolved and cross-checked as one pair
