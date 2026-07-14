# Q871: from clvm collapse distinct inputs into one accepted state via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `from_clvm` in `crates/clvm-traits/src/from_clvm.rs` with CLVM atoms with redundant sign bytes when equivalent-looking encodings are mixed make chia_rs collapse distinct inputs into one accepted state, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-traits/src/from_clvm.rs:126` / `from_clvm`
- Entrypoint: hash curried CLVM programs
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `from_clvm` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
