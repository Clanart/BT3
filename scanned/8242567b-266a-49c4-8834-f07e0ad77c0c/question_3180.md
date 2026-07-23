# Q3180: Reuse commit state inside set_citrea_commit_outpoint

## Question
Can an unprivileged attacker replay or reuse the sequencing of commit/reveal/finalization updates so `set_citrea_commit_outpoint` treats one commit outpoint or aggregate body as authorizing a different reveal path, corrupting the commit outpoint tied to a Citrea raw-tx batch and breaking the invariant that retry/reset logic must not let old commit or reveal state authorize a different body, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_commit_outpoint
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the sequencing of commit/reveal/finalization updates
- Exploit idea: treat one commit outpoint or body as authorizing a different reveal set via the sequencing of commit/reveal/finalization updates
- Invariant to test: retry/reset logic must not let old commit or reveal state authorize a different body
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
