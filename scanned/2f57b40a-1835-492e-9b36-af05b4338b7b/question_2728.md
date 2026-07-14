# Q2728: from bytes collapse distinct inputs into one accepted state via insert/delete operation batches

## Question
Can an unprivileged attacker submit DataLayer proof/blob bytes targeting `from_bytes` in `crates/chia-datalayer/src/merkle/format.rs` with insert/delete operation batches when the payload is accepted by one public API before another validates it make chia_rs collapse distinct inputs into one accepted state, violating the invariant that iterators cannot skip or duplicate nodes, and realistically causing the in-scope impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state?

## Target
- File/function: `crates/chia-datalayer/src/merkle/format.rs:333` / `from_bytes`
- Entrypoint: submit DataLayer proof/blob bytes
- Attacker controls: insert/delete operation batches
- Exploit idea: Drive `from_bytes` through its public caller path using insert/delete operation batches; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: iterators cannot skip or duplicate nodes
- Expected Immunefi impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state
- Fast validation: iterate all nodes and assert no missing or duplicated indexes.
