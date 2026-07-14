# Q943: TreeHasher collapse distinct inputs into one accepted state via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `TreeHasher` in `crates/clvm-utils/src/hash_encoder.rs` with CLVM atoms with redundant sign bytes when the attacker can choose ordering inside a batch make chia_rs collapse distinct inputs into one accepted state, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-utils/src/hash_encoder.rs:22` / `TreeHasher`
- Entrypoint: hash curried CLVM programs
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `TreeHasher` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
