# Q1292: Exploit reset/retry handling in set_citrea_commit_outpoint

## Question
Can an unprivileged attacker use public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path with crafted the Citrea body/chunk payloads so `set_citrea_commit_outpoint` revives stale state after a reset or retry, corrupting the aggregate body hash that should identify the same chunks end-to-end and violating the invariant that retry/reset logic must not let old commit or reveal state authorize a different body, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_commit_outpoint
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the Citrea body/chunk payloads
- Exploit idea: revive stale state after a reset or retry using the Citrea body/chunk payloads
- Invariant to test: retry/reset logic must not let old commit or reveal state authorize a different body
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
