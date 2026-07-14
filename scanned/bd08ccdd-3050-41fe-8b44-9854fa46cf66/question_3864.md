# Q3864: parse named fields reuse stale verification state via improper list terminators

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `parse_named_fields` in `crates/clvm-derive/src/parser/field_info.rs` with improper list terminators when duplicate or prefix-colliding items are present make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-derive/src/parser/field_info.rs:13` / `parse_named_fields`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: improper list terminators
- Exploit idea: Drive `parse_named_fields` through its public caller path using improper list terminators; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
