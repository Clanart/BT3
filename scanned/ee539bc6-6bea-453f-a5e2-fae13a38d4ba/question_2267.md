# Q2267: py get string derive a different canonical hash via partial proof quality strings

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `py_get_string` in `crates/chia-protocol/src/partial_proof.rs` with partial proof quality strings when a node processes data from an untrusted peer or wallet make chia_rs derive a different canonical hash, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:50` / `py_get_string`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `py_get_string` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
