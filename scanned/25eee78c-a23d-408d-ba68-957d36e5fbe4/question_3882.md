# Q3882: `get_reimbursement_txs` does not bind the amount the withdrawer receives

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction reach `get_reimbursement_txs` in `core/src/operator.rs` with an `output_amount` far below the vault's `bridge_amount` (or with the difference absorbed into fees), given the only bound is `output_amount <= bridge_amount - FINALIZED_BLOCK_CONSUMER_ID_AUTOMATION` and `Operator::is_profitable` returns true outright when the input value exceeds the withdrawal amount, so bridged BTC leaves a vault without a matching payment to the user?

## Target
- File/function: `core/src/operator.rs` -> `get_reimbursement_txs`
- Entrypoint: aggregator `OptimisticPayout` / `Withdraw` -> `get_reimbursement_txs`
- Attacker controls: `output_amount`, `output_script_pubkey`, and the value of the withdrawal UTXO; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: move vault value to fees or to an unrelated script instead of to the withdrawer
- Invariant to test: value received by the withdrawer + protocol fee == value leaving the vault
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: call the payout path with a near-zero output amount and assert it is refused
