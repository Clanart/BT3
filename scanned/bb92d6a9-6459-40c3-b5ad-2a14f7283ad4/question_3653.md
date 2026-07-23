# Q3653: Exploit reset/retry handling in insert_citrea_raw_tx_chunks

## Question
Can an unprivileged attacker use public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path with crafted the commit outpoint timing and reuse across retries so `insert_citrea_raw_tx_chunks` revives stale state after a reset or retry, corrupting the aggregate body hash that should identify the same chunks end-to-end and violating the invariant that retry/reset logic must not let old commit or reveal state authorize a different body, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::insert_citrea_raw_tx_chunks
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the commit outpoint timing and reuse across retries
- Exploit idea: revive stale state after a reset or retry using the commit outpoint timing and reuse across retries
- Invariant to test: retry/reset logic must not let old commit or reveal state authorize a different body
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
