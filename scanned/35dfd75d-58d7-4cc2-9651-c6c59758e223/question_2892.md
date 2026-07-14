# Q2892: update digest skip a required validation guard via generated streamable struct bytes

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with generated streamable struct bytes when duplicate or prefix-colliding items are present make chia_rs skip a required validation guard, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:190` / `update_digest`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: generated streamable struct bytes
- Exploit idea: Drive `update_digest` through its public caller path using generated streamable struct bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
