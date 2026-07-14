# Q1336: from json dict mis-bind attacker-controlled bytes to trusted state via macro-generated vector fields

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `from_json_dict` in `crates/chia-traits/src/from_json_dict.rs` with macro-generated vector fields at a fork-height or boundary-value activation point make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/from_json_dict.rs:66` / `from_json_dict`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: macro-generated vector fields
- Exploit idea: Drive `from_json_dict` through its public caller path using macro-generated vector fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
