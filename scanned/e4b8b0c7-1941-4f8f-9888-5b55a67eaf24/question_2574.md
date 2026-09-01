# Q2574: `send_citrea_tx` and anchor/CPFP spending

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees spend or race the anchor output that `send_citrea_tx` in `crates/clementine-tx-sender/src/client.rs` relies on for CPFP, or make the child package non-standard, so the parent bridge transaction cannot be accelerated before its deadline?

## Target
- File/function: `crates/clementine-tx-sender/src/client.rs` -> `send_citrea_tx` (This module is provides a client which is responsible for inserting)
- Entrypoint: a Bitcoin transaction spending or conflicting with the anchor -> `send_citrea_tx`
- Attacker controls: the competing anchor spend, its fee and its size; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: disable the acceleration path for a deadline-bound bridge transaction
- Invariant to test: the CPFP path remains available to the bridge for the whole deadline window
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: race the anchor spend in regtest and assert the parent still confirms
