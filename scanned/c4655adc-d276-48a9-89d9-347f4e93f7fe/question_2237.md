# Q2237: Misbind reveal-script semantics in insert_citrea_raw_tx_chunks

## Question
Can an unprivileged attacker craft the Citrea body/chunk payloads so `insert_citrea_raw_tx_chunks` no longer ties reveal scripts to the same chunks or body that were committed, corrupting the aggregate body hash that should identify the same chunks end-to-end and violating the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::insert_citrea_raw_tx_chunks
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the Citrea body/chunk payloads
- Exploit idea: disconnect reveal scripts from the chunks/body that were committed via the Citrea body/chunk payloads
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
