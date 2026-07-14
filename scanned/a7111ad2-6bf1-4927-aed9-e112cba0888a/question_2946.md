# Q2946: deepcopy treat malformed data as a valid empty/default value via generated streamable struct bytes

## Question
Can an unprivileged attacker compute streamable hashes targeting `__deepcopy__` in `crates/chia_py_streamable_macro/src/lib.rs` with generated streamable struct bytes when the payload is accepted by one public API before another validates it make chia_rs treat malformed data as a valid empty/default value, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:386` / `__deepcopy__`
- Entrypoint: compute streamable hashes
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `__deepcopy__` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
