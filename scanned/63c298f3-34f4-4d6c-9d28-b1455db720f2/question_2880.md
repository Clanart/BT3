# Q2880: update digest skip a required validation guard via generated streamable struct bytes

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with generated streamable struct bytes when the same payload is parsed through public bindings make chia_rs skip a required validation guard, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:79` / `update_digest`
- Entrypoint: parse generated streamable bytes
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `update_digest` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
