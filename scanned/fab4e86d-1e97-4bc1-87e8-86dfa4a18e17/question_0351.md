# Q0351: `get_bitvm_setup` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `get_bitvm_setup` in `core/src/database/operator.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/operator.rs` -> `get_bitvm_setup` (This module includes database functions which are mainly used by an operator)
- Entrypoint: concurrent aggregator requests or on-chain events -> `get_bitvm_setup`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `get_bitvm_setup` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `get_bitvm_setup` and assert a constraint rejects the duplicate
