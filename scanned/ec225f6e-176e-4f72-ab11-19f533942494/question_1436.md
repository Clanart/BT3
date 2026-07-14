# Q1436: from json dict overflow or underflow a boundary check via hash/update digest inputs

## Question
Can an unprivileged attacker compute streamable hashes targeting `from_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with hash/update_digest inputs with default-enabled consensus flags make chia_rs overflow or underflow a boundary check, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:530` / `from_json_dict`
- Entrypoint: compute streamable hashes
- Attacker controls: hash/update_digest inputs
- Exploit idea: Drive `from_json_dict` through its public caller path using hash/update_digest inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
