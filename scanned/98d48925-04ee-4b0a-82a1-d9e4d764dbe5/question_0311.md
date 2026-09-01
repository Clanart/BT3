# Q0311: `begin_transaction` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `begin_transaction` in `core/src/database/mod.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/mod.rs` -> `begin_transaction` (Database crate provides functions that adds/reads values from PostgreSQL)
- Entrypoint: concurrent aggregator requests or on-chain events -> `begin_transaction`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `begin_transaction` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `begin_transaction` and assert a constraint rejects the duplicate
