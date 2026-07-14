# Q1362: update digest reuse stale verification state via newtype and enum field encodings

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with newtype and enum field encodings when duplicate or prefix-colliding items are present make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:107` / `update_digest`
- Entrypoint: parse generated streamable bytes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `update_digest` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
