# Q2627: `get_reimbursement_txs` and one-payout-per-vault accounting

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction arrange for a single on-chain payout transaction to be counted twice by `get_reimbursement_txs` in `core/src/operator.rs` - once through the optimistic path and once through the kickoff/reimbursement path, or under two withdrawal indices that name the same UTXO - so more than one vault UTXO is spent for one settled withdrawal?

## Target
- File/function: `core/src/operator.rs` -> `get_reimbursement_txs`
- Entrypoint: aggregator `Withdraw` + `OptimisticPayout` for related indices -> `get_reimbursement_txs`
- Attacker controls: the withdrawal indices submitted, the withdrawal UTXO reused across them, and request ordering; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: drain a second 10 BTC vault for a withdrawal that was settled once
- Invariant to test: the number of vault UTXOs spent for one settled withdrawal == 1
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: drive both settlement paths for one withdrawal in a mocked-Citrea test and assert the second is refused
