# Q2636: py insert allow replay across contexts via proof-of-inclusion paths

## Question
Can an unprivileged attacker iterate over attacker-controlled Merkle blobs targeting `py_insert` in `crates/chia-datalayer/src/merkle/blob.rs` with proof-of-inclusion paths with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-datalayer/src/merkle/blob.rs:1410` / `py_insert`
- Entrypoint: iterate over attacker-controlled Merkle blobs
- Attacker controls: proof-of-inclusion paths
- Exploit idea: Drive `py_insert` through its public caller path using proof-of-inclusion paths; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz blob/delta/proof bytes and compare roots to a reference model.
