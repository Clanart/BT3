# Q2847: `encode_by_ref` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `encode_by_ref` in `core/src/database/wrapper.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/wrapper.rs` -> `encode_by_ref` (This module includes wrappers for easy parsing of the foreign types)
- Entrypoint: concurrent aggregator requests or on-chain events -> `encode_by_ref`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `encode_by_ref` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `encode_by_ref` and assert a constraint rejects the duplicate
