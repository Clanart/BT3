# Q2708: Mix chunk order inside set_citrea_commit_outpoint

## Question
Can an unprivileged attacker craft the commit outpoint timing and reuse across retries so `set_citrea_commit_outpoint` keeps chunk ordering or chunk identity consistent in one place but not another, corrupting the aggregate body hash that should identify the same chunks end-to-end and violating the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_commit_outpoint
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the commit outpoint timing and reuse across retries
- Exploit idea: keep chunk identity consistent in one place but not another using the commit outpoint timing and reuse across retries
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
