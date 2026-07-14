# Q1365: update digest treat malformed data as a valid empty/default value via trusted parse flags

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with trusted parse flags when serialized bytes are validly framed but semantically adversarial make chia_rs treat malformed data as a valid empty/default value, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:143` / `update_digest`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: trusted parse flags
- Exploit idea: Drive `update_digest` through its public caller path using trusted parse flags; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
