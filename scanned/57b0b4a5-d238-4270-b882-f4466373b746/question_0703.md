# Q0703: `get_withdrawal_utxo_from_citrea_withdrawal` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `get_withdrawal_utxo_from_citrea_withdrawal` in `core/src/database/verifier.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/verifier.rs` -> `get_withdrawal_utxo_from_citrea_withdrawal` (This module includes database functions which are mainly used by a verifier)
- Entrypoint: concurrent aggregator requests or on-chain events -> `get_withdrawal_utxo_from_citrea_withdrawal`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `get_withdrawal_utxo_from_citrea_withdrawal` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `get_withdrawal_utxo_from_citrea_withdrawal` and assert a constraint rejects the duplicate
