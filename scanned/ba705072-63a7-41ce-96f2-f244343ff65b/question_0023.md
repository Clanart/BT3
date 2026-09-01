# Q0023: `check_if_utxo_spending_tx_is_finalized` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `check_if_utxo_spending_tx_is_finalized` in `core/src/database/bitcoin_syncer.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/bitcoin_syncer.rs` -> `check_if_utxo_spending_tx_is_finalized`
- Entrypoint: concurrent aggregator requests or on-chain events -> `check_if_utxo_spending_tx_is_finalized`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `check_if_utxo_spending_tx_is_finalized` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `check_if_utxo_spending_tx_is_finalized` and assert a constraint rejects the duplicate
