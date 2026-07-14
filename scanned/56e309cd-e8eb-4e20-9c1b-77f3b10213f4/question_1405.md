# Q1405: richcmp accept invalid consensus data via generated streamable struct bytes

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `__richcmp__` in `crates/chia_py_streamable_macro/src/lib.rs` with generated streamable struct bytes when a node processes data from an untrusted peer or wallet make chia_rs accept invalid consensus data, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:90` / `__richcmp__`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `__richcmp__` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
