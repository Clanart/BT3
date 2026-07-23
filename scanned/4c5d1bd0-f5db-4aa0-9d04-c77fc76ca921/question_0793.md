# Q793: Reuse commit state inside set_citrea_aggregate_finalized

## Question
Can an unprivileged attacker replay or reuse the commit outpoint timing and reuse across retries so `set_citrea_aggregate_finalized` treats one commit outpoint or aggregate body as authorizing a different reveal path, corrupting the aggregate body hash that should identify the same chunks end-to-end and breaking the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_aggregate_finalized
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the commit outpoint timing and reuse across retries
- Exploit idea: treat one commit outpoint or body as authorizing a different reveal set via the commit outpoint timing and reuse across retries
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
