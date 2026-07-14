# Q2921: maybe upper fields overflow or underflow a boundary check via newtype and enum field encodings

## Question
Can an unprivileged attacker compute streamable hashes targeting `maybe_upper_fields` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when values sit exactly at max/min integer boundaries make chia_rs overflow or underflow a boundary check, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:8` / `maybe_upper_fields`
- Entrypoint: compute streamable hashes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `maybe_upper_fields` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
