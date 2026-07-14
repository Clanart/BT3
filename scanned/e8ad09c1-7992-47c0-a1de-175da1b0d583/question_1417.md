# Q1417: to json dict accept invalid consensus data via generated streamable struct bytes

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `to_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with generated streamable struct bytes when the payload is accepted by one public API before another validates it make chia_rs accept invalid consensus data, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:262` / `to_json_dict`
- Entrypoint: parse generated streamable bytes
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `to_json_dict` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
