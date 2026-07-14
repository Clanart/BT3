# Q1416: from json dict commit output after an error path via newtype and enum field encodings

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `from_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when the payload is accepted by one public API before another validates it make chia_rs commit output after an error path, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:245` / `from_json_dict`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `from_json_dict` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
