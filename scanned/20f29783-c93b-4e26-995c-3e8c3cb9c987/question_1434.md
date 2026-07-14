# Q1434: from json dict reuse stale verification state via newtype and enum field encodings

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `from_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings with default-enabled consensus flags make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:505` / `from_json_dict`
- Entrypoint: parse generated streamable bytes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `from_json_dict` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
