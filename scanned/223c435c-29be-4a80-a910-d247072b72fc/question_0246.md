# Q0246: `get_confirmed_fee_payer_utxos` and the fee rate it derives

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees influence the fee estimate `get_confirmed_fee_payer_utxos` in `crates/clementine-tx-sender/src/cpfp.rs` uses (mempool conditions, the configured multiplier/offset, the hard cap) so a bridge transaction is under-funded past a deadline, or over-funded to the point that bridge-controlled value is burned as fees?

## Target
- File/function: `crates/clementine-tx-sender/src/cpfp.rs` -> `get_confirmed_fee_payer_utxos` (This module implements the Child Pays For Parent (CPFP) strategy for sending)
- Entrypoint: mempool manipulation observable to the entity -> `get_confirmed_fee_payer_utxos`
- Attacker controls: mempool composition and the resulting fee estimate; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: starve or drain a bridge transaction through its fee estimate
- Invariant to test: the fee paid stays within the configured bounds and always suffices before the deadline
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: drive the estimator to both extremes and assert bounds hold
