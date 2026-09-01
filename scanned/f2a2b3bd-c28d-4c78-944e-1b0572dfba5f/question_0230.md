# Q0230: `create_fee_payer_utxo` and anchor/CPFP spending

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees spend or race the anchor output that `create_fee_payer_utxo` in `crates/clementine-tx-sender/src/cpfp.rs` relies on for CPFP, or make the child package non-standard, so the parent bridge transaction cannot be accelerated before its deadline?

## Target
- File/function: `crates/clementine-tx-sender/src/cpfp.rs` -> `create_fee_payer_utxo` (This module implements the Child Pays For Parent (CPFP) strategy for sending)
- Entrypoint: a Bitcoin transaction spending or conflicting with the anchor -> `create_fee_payer_utxo`
- Attacker controls: the competing anchor spend, its fee and its size; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: disable the acceleration path for a deadline-bound bridge transaction
- Invariant to test: the CPFP path remains available to the bridge for the whole deadline window
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: race the anchor spend in regtest and assert the parent still confirms
