# Q3984: tree hash reuse stale verification state via improper list terminators

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `tree_hash` in `crates/clvm-utils/src/hash_encoder.rs` with improper list terminators when serialized bytes are validly framed but semantically adversarial make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/hash_encoder.rs:16` / `tree_hash`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: improper list terminators
- Exploit idea: Drive `tree_hash` through its public caller path using improper list terminators; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
