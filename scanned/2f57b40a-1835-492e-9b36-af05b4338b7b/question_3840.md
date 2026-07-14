# Q3840: check rest value reuse stale verification state via improper list terminators

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `check_rest_value` in `crates/clvm-derive/src/from_clvm.rs` with improper list terminators at a fork-height or boundary-value activation point make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-derive/src/from_clvm.rs:150` / `check_rest_value`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: improper list terminators
- Exploit idea: Drive `check_rest_value` through its public caller path using improper list terminators; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
