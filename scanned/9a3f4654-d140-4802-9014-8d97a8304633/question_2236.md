# Q2236: Misbind reveal-script semantics in set_citrea_commit_outpoint

## Question
Can an unprivileged attacker craft the reveal-script chunk ordering and chunk sizes so `set_citrea_commit_outpoint` no longer ties reveal scripts to the same chunks or body that were committed, corrupting the commit/reveal linkage for a Citrea body and violating the invariant that retry/reset logic must not let old commit or reveal state authorize a different body, leading to Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_commit_outpoint
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the reveal-script chunk ordering and chunk sizes
- Exploit idea: disconnect reveal scripts from the chunks/body that were committed via the reveal-script chunk ordering and chunk sizes
- Invariant to test: retry/reset logic must not let old commit or reveal state authorize a different body
- Expected Immunefi impact: Medium. Griefing that causes large-scale disruption of deposits/withdrawals without theft other than by the Aggregator
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
