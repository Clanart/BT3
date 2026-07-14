# Q2854: from json dict accept invalid consensus data via JSON dictionary values

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `from_json_dict` in `crates/chia-traits/src/from_json_dict.rs` with JSON dictionary values with default-enabled consensus flags make chia_rs accept invalid consensus data, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/from_json_dict.rs:17` / `from_json_dict`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `from_json_dict` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
