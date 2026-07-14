# Q2953: from json dict mis-bind attacker-controlled bytes to trusted state via hash/update digest inputs

## Question
Can an unprivileged attacker compute streamable hashes targeting `from_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with hash/update_digest inputs when equivalent-looking encodings are mixed make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:467` / `from_json_dict`
- Entrypoint: compute streamable hashes
- Attacker controls: hash/update_digest inputs
- Exploit idea: Drive `from_json_dict` through its public caller path using hash/update_digest inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
