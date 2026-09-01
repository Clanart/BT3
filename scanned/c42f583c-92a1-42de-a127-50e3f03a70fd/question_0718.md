# Q0718: `set_cancel_outpoint_finalized` and Citrea-bound relaying

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees shape the data `set_cancel_outpoint_finalized` in `crates/clementine-tx-sender/src/db/tx_sender.rs` relays to or from Citrea (reveal scripts, serialized payloads, sync ranges) so the L2 record the bridge depends on differs from what the L1 transaction actually says?

## Target
- File/function: `crates/clementine-tx-sender/src/db/tx_sender.rs` -> `set_cancel_outpoint_finalized` (SQLx queries for tx-sender tables)
- Entrypoint: attacker-shaped L1/L2 data -> `set_cancel_outpoint_finalized`
- Attacker controls: the payload bytes and the height ranges relayed; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: make L1 and L2 records of the same fact disagree
- Invariant to test: the record written on Citrea == the fact confirmed on Bitcoin
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: assert round-trip equality of relayed payloads for adversarial inputs
