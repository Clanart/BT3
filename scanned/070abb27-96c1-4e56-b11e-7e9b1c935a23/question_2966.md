# Q2966: update digest produce a Rust/Python disagreement via trusted parse flags

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `update_digest` in `crates/chia_streamable_macro/src/lib.rs` with trusted parse flags with default-enabled consensus flags make chia_rs produce a Rust/Python disagreement, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:234` / `update_digest`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: trusted parse flags
- Exploit idea: Drive `update_digest` through its public caller path using trusted parse flags; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
