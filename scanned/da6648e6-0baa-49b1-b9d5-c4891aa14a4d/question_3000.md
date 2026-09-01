# Q3000: `get_operator_clients` and one reimbursement per fronted payout

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction cause `get_operator_clients` in `core/src/aggregator.rs` to mark more than one kickoff/reimbursement path as eligible for a single settled withdrawal - by reusing a connector, by racing two rounds, or by making the payout record visible to two paths - so bridged BTC leaves two vaults for one payout?

## Target
- File/function: `core/src/aggregator.rs` -> `get_operator_clients`
- Entrypoint: aggregator `Withdraw` plus attacker-timed Bitcoin transactions -> `get_operator_clients`
- Attacker controls: withdrawal registration timing and the on-chain transactions that trigger settlement bookkeeping; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: extract a second vault for one settled withdrawal
- Invariant to test: settled withdrawals == vaults spent, one to one
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: drive concurrent settlement paths for one withdrawal and assert exactly one vault is spent
