# Q3887: decode bigint produce a Rust/Python disagreement via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `decode_bigint` in `crates/clvm-traits/src/clvm_decoder.rs` with CLVM atoms with redundant sign bytes when the attacker can choose ordering inside a batch make chia_rs produce a Rust/Python disagreement, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/clvm_decoder.rs:59` / `decode_bigint`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `decode_bigint` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
