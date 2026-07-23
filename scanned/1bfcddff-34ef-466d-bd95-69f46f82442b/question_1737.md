# Q1737: Break hash binding in set_citrea_aggregate_finalized

## Question
Can an unprivileged attacker shape the Citrea body/chunk payloads so `set_citrea_aggregate_finalized` accepts two semantically different payloads under one hash or one payload under two inconsistent interpretations, corrupting the commit/reveal linkage for a Citrea body and breaking the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Critical. Direct loss of funds?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::set_citrea_aggregate_finalized
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the Citrea body/chunk payloads
- Exploit idea: make two payload interpretations survive under one attacker-controlled the Citrea body/chunk payloads
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
