# Q4014: `get_stopped_tasks` and a prover-chosen payout input index

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction shape a payout transaction such that the input index used by `get_stopped_tasks` in `core/src/task/manager.rs` to bind the withdrawal outpoint is not the input that actually pays the user - multiple inputs, an index out of range, or an index pointing at an unrelated attacker input - so the settlement binding is satisfied by a transaction that pays nobody?

## Target
- File/function: `core/src/task/manager.rs` -> `get_stopped_tasks`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `get_stopped_tasks`
- Attacker controls: the payout transaction's input list and ordering; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: satisfy the withdrawal binding with an input the withdrawer never benefited from
- Invariant to test: the input the settlement is bound to == the input spending the registered withdrawal UTXO, and that transaction pays the withdrawer
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: construct a multi-input payout and assert the binding is checked at the correct index
