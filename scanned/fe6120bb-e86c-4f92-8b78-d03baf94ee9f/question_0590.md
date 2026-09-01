# Q0590: `list_unfinalized_try_to_send_txs` and anchor/CPFP spending

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees spend or race the anchor output that `list_unfinalized_try_to_send_txs` in `crates/clementine-tx-sender/src/db/tx_sender.rs` relies on for CPFP, or make the child package non-standard, so the parent bridge transaction cannot be accelerated before its deadline?

## Target
- File/function: `crates/clementine-tx-sender/src/db/tx_sender.rs` -> `list_unfinalized_try_to_send_txs` (SQLx queries for tx-sender tables)
- Entrypoint: a Bitcoin transaction spending or conflicting with the anchor -> `list_unfinalized_try_to_send_txs`
- Attacker controls: the competing anchor spend, its fee and its size; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: disable the acceleration path for a deadline-bound bridge transaction
- Invariant to test: the CPFP path remains available to the bridge for the whole deadline window
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: race the anchor spend in regtest and assert the parent still confirms
