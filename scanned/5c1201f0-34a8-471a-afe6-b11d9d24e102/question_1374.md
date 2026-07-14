# Q1374: update digest reuse stale verification state via newtype and enum field encodings

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with newtype and enum field encodings when serialized bytes are validly framed but semantically adversarial make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:200` / `update_digest`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `update_digest` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
