# Q2303: `upsert_move_to_vault_txid_from_citrea_deposit` and the uniqueness of a single-use record

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port drive two concurrent flows into `upsert_move_to_vault_txid_from_citrea_deposit` in `core/src/database/verifier.rs` so a record that must be unique (a served withdrawal, a used connector, a deposit-to-vault mapping) is written twice or upserted over, letting one on-chain fact be settled twice?

## Target
- File/function: `core/src/database/verifier.rs` -> `upsert_move_to_vault_txid_from_citrea_deposit` (This module includes database functions which are mainly used by a verifier)
- Entrypoint: concurrent aggregator requests or on-chain events -> `upsert_move_to_vault_txid_from_citrea_deposit`
- Attacker controls: request concurrency and the ordering of on-chain events; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: duplicate a single-use protocol record
- Invariant to test: the table `upsert_move_to_vault_txid_from_citrea_deposit` writes enforces one row per protocol fact under concurrent writers
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run concurrent writers against `upsert_move_to_vault_txid_from_citrea_deposit` and assert a constraint rejects the duplicate
