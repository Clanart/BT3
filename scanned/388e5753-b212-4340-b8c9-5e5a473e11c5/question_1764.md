# Q1764: Break hash binding in set_citrea_commit_outpoint

## Question
Can an unprivileged attacker shape the aggregate commit body and its hash linkage so `set_citrea_commit_outpoint` accepts two semantically different payloads under one hash or one payload under two inconsistent interpretations, corrupting the commit outpoint tied to a Citrea raw-tx batch and breaking the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_commit_outpoint
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the aggregate commit body and its hash linkage
- Exploit idea: make two payload interpretations survive under one attacker-controlled the aggregate commit body and its hash linkage
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
