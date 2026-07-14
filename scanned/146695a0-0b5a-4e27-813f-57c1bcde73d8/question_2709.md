# Q2709: NodeType commit output after an error path via tree index values near block boundaries

## Question
Can an unprivileged attacker verify inclusion/exclusion proofs targeting `NodeType` in `crates/chia-datalayer/src/merkle/format.rs` with tree index values near block boundaries when a node processes data from an untrusted peer or wallet make chia_rs commit output after an error path, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state?

## Target
- File/function: `crates/chia-datalayer/src/merkle/format.rs:156` / `NodeType`
- Entrypoint: verify inclusion/exclusion proofs
- Attacker controls: tree index values near block boundaries
- Exploit idea: Drive `NodeType` through its public caller path using tree index values near block boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state
- Fast validation: iterate all nodes and assert no missing or duplicated indexes.
