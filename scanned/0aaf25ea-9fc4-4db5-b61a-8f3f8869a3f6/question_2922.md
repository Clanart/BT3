# Q2922: is vec type treat malformed data as a valid empty/default value via generated streamable struct bytes

## Question
Can an unprivileged attacker compute streamable hashes targeting `is_vec_type` in `crates/chia_py_streamable_macro/src/lib.rs` with generated streamable struct bytes when values sit exactly at max/min integer boundaries make chia_rs treat malformed data as a valid empty/default value, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:19` / `is_vec_type`
- Entrypoint: compute streamable hashes
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `is_vec_type` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
