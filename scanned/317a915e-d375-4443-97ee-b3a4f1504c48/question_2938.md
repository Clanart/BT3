# Q2938: to json dict accept invalid consensus data via JSON dictionary values

## Question
Can an unprivileged attacker compute streamable hashes targeting `to_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with JSON dictionary values when the payload is accepted by one public API before another validates it make chia_rs accept invalid consensus data, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:262` / `to_json_dict`
- Entrypoint: compute streamable hashes
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `to_json_dict` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
