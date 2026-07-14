# Q2963: update digest derive a different canonical hash via newtype and enum field encodings

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `update_digest` in `crates/chia_streamable_macro/src/lib.rs` with newtype and enum field encodings with default-enabled consensus flags make chia_rs derive a different canonical hash, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:194` / `update_digest`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `update_digest` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
