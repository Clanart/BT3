# Q1430: py json dict macro derive a different canonical hash via hash/update digest inputs

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `py_json_dict_macro` in `crates/chia_py_streamable_macro/src/lib.rs` with hash/update_digest inputs when equivalent-looking encodings are mixed make chia_rs derive a different canonical hash, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:434` / `py_json_dict_macro`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: hash/update_digest inputs
- Exploit idea: Drive `py_json_dict_macro` through its public caller path using hash/update_digest inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
