# Q2952: to json dict skip a required validation guard via generated streamable struct bytes

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `to_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with generated streamable struct bytes when equivalent-looking encodings are mixed make chia_rs skip a required validation guard, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:461` / `to_json_dict`
- Entrypoint: parse generated streamable bytes
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `to_json_dict` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
