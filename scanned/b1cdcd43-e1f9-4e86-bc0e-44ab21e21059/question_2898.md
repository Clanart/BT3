# Q2898: update digest treat malformed data as a valid empty/default value via generated streamable struct bytes

## Question
Can an unprivileged attacker compute streamable hashes targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with generated streamable struct bytes when serialized bytes are validly framed but semantically adversarial make chia_rs treat malformed data as a valid empty/default value, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:235` / `update_digest`
- Entrypoint: compute streamable hashes
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `update_digest` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
