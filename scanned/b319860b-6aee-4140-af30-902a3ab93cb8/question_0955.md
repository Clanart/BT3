# Q955: as ref collapse distinct inputs into one accepted state via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `as_ref` in `crates/clvm-utils/src/tree_hash.rs` with CLVM atoms with redundant sign bytes when values sit exactly at max/min integer boundaries make chia_rs collapse distinct inputs into one accepted state, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:51` / `as_ref`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `as_ref` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
