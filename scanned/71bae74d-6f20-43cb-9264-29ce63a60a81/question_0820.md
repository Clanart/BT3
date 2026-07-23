# Q820: Reuse commit state inside set_citrea_commit_outpoint

## Question
Can an unprivileged attacker replay or reuse the sequencing of commit/reveal/finalization updates so `set_citrea_commit_outpoint` treats one commit outpoint or aggregate body as authorizing a different reveal path, corrupting the commit/reveal linkage for a Citrea body and breaking the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_commit_outpoint
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the sequencing of commit/reveal/finalization updates
- Exploit idea: treat one commit outpoint or body as authorizing a different reveal set via the sequencing of commit/reveal/finalization updates
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
