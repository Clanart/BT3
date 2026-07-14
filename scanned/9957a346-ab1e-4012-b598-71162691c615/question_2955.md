# Q2955: from json dict reuse stale verification state via macro-generated vector fields

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `from_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with macro-generated vector fields when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:505` / `from_json_dict`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: macro-generated vector fields
- Exploit idea: Drive `from_json_dict` through its public caller path using macro-generated vector fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
