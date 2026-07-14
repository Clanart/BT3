# Q1069: new accept invalid consensus data via Merkle blob bytes

## Question
Can an unprivileged attacker iterate over attacker-controlled Merkle blobs targeting `new` in `crates/chia-datalayer/src/merkle/blob.rs` with Merkle blob bytes when a node processes data from an untrusted peer or wallet make chia_rs accept invalid consensus data, violating the invariant that proofs bind key/value data to the claimed root, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-datalayer/src/merkle/blob.rs:316` / `new`
- Entrypoint: iterate over attacker-controlled Merkle blobs
- Attacker controls: Merkle blob bytes
- Exploit idea: Drive `new` through its public caller path using Merkle blob bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proofs bind key/value data to the claimed root
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate sibling paths and assert proof rejection.
