# Q2647: `is_pgmq_installed` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `is_pgmq_installed` in `core/src/database/mod.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/mod.rs` -> `is_pgmq_installed` (Database crate provides functions that adds/reads values from PostgreSQL)
- Entrypoint: concurrent aggregator requests or on-chain events -> `is_pgmq_installed`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `is_pgmq_installed` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `is_pgmq_installed` and assert a constraint rejects the duplicate
