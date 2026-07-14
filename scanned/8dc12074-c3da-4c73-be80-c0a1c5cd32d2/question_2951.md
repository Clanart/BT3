# Q2951: py json dict macro derive a different canonical hash via newtype and enum field encodings

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `py_json_dict_macro` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when equivalent-looking encodings are mixed make chia_rs derive a different canonical hash, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:434` / `py_json_dict_macro`
- Entrypoint: parse generated streamable bytes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `py_json_dict_macro` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
