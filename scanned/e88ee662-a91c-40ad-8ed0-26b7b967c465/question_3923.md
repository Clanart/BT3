# Q3923: decode produce a Rust/Python disagreement via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `decode` in `crates/clvm-traits/src/from_clvm.rs` with CLVM atoms with redundant sign bytes when the payload is accepted by one public API before another validates it make chia_rs produce a Rust/Python disagreement, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-traits/src/from_clvm.rs:301` / `decode`
- Entrypoint: hash curried CLVM programs
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `decode` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
