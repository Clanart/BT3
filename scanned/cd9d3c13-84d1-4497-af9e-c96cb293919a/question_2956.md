# Q2956: to json dict collapse distinct inputs into one accepted state via JSON dictionary values

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `to_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with JSON dictionary values when equivalent-looking encodings are mixed make chia_rs collapse distinct inputs into one accepted state, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:524` / `to_json_dict`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `to_json_dict` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
