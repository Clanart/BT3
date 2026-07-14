# Q3788: py get string derive a different canonical hash via plot iteration boundary values

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `py_get_string` in `crates/chia-protocol/src/partial_proof.rs` with plot iteration boundary values when values sit exactly at max/min integer boundaries make chia_rs derive a different canonical hash, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:50` / `py_get_string`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: plot iteration boundary values
- Exploit idea: Drive `py_get_string` through its public caller path using plot iteration boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare quality string outputs across Rust and Python bindings.
