# Q2708: py new allow replay across contexts via proof-of-inclusion paths

## Question
Can an unprivileged attacker iterate over attacker-controlled Merkle blobs targeting `py_new` in `crates/chia-datalayer/src/merkle/format.rs` with proof-of-inclusion paths when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state?

## Target
- File/function: `crates/chia-datalayer/src/merkle/format.rs:114` / `py_new`
- Entrypoint: iterate over attacker-controlled Merkle blobs
- Attacker controls: proof-of-inclusion paths
- Exploit idea: Drive `py_new` through its public caller path using proof-of-inclusion paths; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state
- Fast validation: iterate all nodes and assert no missing or duplicated indexes.
