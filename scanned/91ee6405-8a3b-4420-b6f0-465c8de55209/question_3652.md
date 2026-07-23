# Q3652: Exploit reset/retry handling in set_citrea_commit_outpoint

## Question
Can an unprivileged attacker use public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path with crafted the Citrea body/chunk payloads so `set_citrea_commit_outpoint` revives stale state after a reset or retry, corrupting the commit/reveal linkage for a Citrea body and violating the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_commit_outpoint
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the Citrea body/chunk payloads
- Exploit idea: revive stale state after a reset or retry using the Citrea body/chunk payloads
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
