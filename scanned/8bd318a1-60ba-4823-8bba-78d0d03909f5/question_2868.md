# Q2868: `insert_signed_emergency_stop_tx_if_not_exists` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `insert_signed_emergency_stop_tx_if_not_exists` in `core/src/database/aggregator.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/aggregator.rs` -> `insert_signed_emergency_stop_tx_if_not_exists` (This module includes database functions which are mainly used by a verifier)
- Entrypoint: concurrent aggregator requests or on-chain events -> `insert_signed_emergency_stop_tx_if_not_exists`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `insert_signed_emergency_stop_tx_if_not_exists` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `insert_signed_emergency_stop_tx_if_not_exists` and assert a constraint rejects the duplicate
