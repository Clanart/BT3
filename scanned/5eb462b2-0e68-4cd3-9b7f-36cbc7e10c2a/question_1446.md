# Q1446: stream reuse stale verification state via newtype and enum field encodings

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `stream` in `crates/chia_streamable_macro/src/lib.rs` with newtype and enum field encodings at a fork-height or boundary-value activation point make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:237` / `stream`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `stream` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
