# Q2637: py upsert commit output after an error path via tree index values near block boundaries

## Question
Can an unprivileged attacker verify inclusion/exclusion proofs targeting `py_upsert` in `crates/chia-datalayer/src/merkle/blob.rs` with tree index values near block boundaries with default-enabled consensus flags make chia_rs commit output after an error path, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-datalayer/src/merkle/blob.rs:1436` / `py_upsert`
- Entrypoint: verify inclusion/exclusion proofs
- Attacker controls: tree index values near block boundaries
- Exploit idea: Drive `py_upsert` through its public caller path using tree index values near block boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz blob/delta/proof bytes and compare roots to a reference model.
