# Q2710: Mix chunk order inside clear_citrea_commit_and_try_to_send_by_ids

## Question
Can an unprivileged attacker craft the aggregate commit body and its hash linkage so `clear_citrea_commit_and_try_to_send_by_ids` keeps chunk ordering or chunk identity consistent in one place but not another, corrupting the commit outpoint tied to a Citrea raw-tx batch and violating the invariant that every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/db/citrea.rs::clear_citrea_commit_and_try_to_send_by_ids
- Entrypoint: public JSON-RPC `send_citrea_tx` request or a user-triggered Citrea commit/reveal automation path
- Attacker controls: the aggregate commit body and its hash linkage
- Exploit idea: keep chunk identity consistent in one place but not another using the aggregate commit body and its hash linkage
- Invariant to test: every chunk, aggregate body, commit outpoint, and reveal transaction must stay linked to the same Citrea payload
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: add a Rust test that mutates chunk order, body hash, and commit-outpoint reuse across retries and assert no mixed commit/reveal state survives
