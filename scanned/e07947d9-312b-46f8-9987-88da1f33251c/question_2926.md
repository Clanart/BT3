# Q2926: richcmp accept invalid consensus data via JSON dictionary values

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `__richcmp__` in `crates/chia_py_streamable_macro/src/lib.rs` with JSON dictionary values when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:90` / `__richcmp__`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `__richcmp__` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
