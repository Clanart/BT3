# Q2571: new reuse stale verification state via tree index values near block boundaries

## Question
Can an unprivileged attacker iterate over attacker-controlled Merkle blobs targeting `new` in `crates/chia-datalayer/src/merkle/blob.rs` with tree index values near block boundaries when serialized bytes are validly framed but semantically adversarial make chia_rs reuse stale verification state, violating the invariant that delta application preserves root consistency, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-datalayer/src/merkle/blob.rs:97` / `new`
- Entrypoint: iterate over attacker-controlled Merkle blobs
- Attacker controls: tree index values near block boundaries
- Exploit idea: Drive `new` through its public caller path using tree index values near block boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: delta application preserves root consistency
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: apply insert/delete batches in different orders and compare expected roots.
