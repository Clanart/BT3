# Q954: from reuse stale verification state via big integer encodings

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `from` in `crates/clvm-utils/src/tree_hash.rs` with big integer encodings when values sit exactly at max/min integer boundaries make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:45` / `from`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: big integer encodings
- Exploit idea: Drive `from` through its public caller path using big integer encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
