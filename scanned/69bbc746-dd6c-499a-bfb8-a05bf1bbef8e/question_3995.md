# Q3995: from produce a Rust/Python disagreement via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `from` in `crates/clvm-utils/src/tree_hash.rs` with CLVM atoms with redundant sign bytes when the attacker can choose ordering inside a batch make chia_rs produce a Rust/Python disagreement, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:39` / `from`
- Entrypoint: hash curried CLVM programs
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `from` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
