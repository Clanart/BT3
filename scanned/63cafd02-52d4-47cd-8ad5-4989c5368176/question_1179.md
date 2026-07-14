# Q1179: py new skip a required validation guard via proof-of-inclusion paths

## Question
Can an unprivileged attacker apply DataLayer delta operations targeting `py_new` in `crates/chia-datalayer/src/merkle/format.rs` with proof-of-inclusion paths when a node processes data from an untrusted peer or wallet make chia_rs skip a required validation guard, violating the invariant that proofs bind key/value data to the claimed root, and realistically causing the in-scope impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state?

## Target
- File/function: `crates/chia-datalayer/src/merkle/format.rs:34` / `py_new`
- Entrypoint: apply DataLayer delta operations
- Attacker controls: proof-of-inclusion paths
- Exploit idea: Drive `py_new` through its public caller path using proof-of-inclusion paths; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proofs bind key/value data to the claimed root
- Expected Immunefi impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state
- Fast validation: apply insert/delete batches in different orders and compare expected roots.
