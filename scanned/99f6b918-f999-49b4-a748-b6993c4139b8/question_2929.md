# Q2929: py new mis-bind attacker-controlled bytes to trusted state via hash/update digest inputs

## Question
Can an unprivileged attacker compute streamable hashes targeting `py_new` in `crates/chia_py_streamable_macro/src/lib.rs` with hash/update_digest inputs when a node processes data from an untrusted peer or wallet make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:133` / `py_new`
- Entrypoint: compute streamable hashes
- Attacker controls: hash/update_digest inputs
- Exploit idea: Drive `py_new` through its public caller path using hash/update_digest inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
