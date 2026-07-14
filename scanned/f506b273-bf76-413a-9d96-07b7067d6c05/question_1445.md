# Q1445: update digest produce a Rust/Python disagreement via JSON dictionary values

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `update_digest` in `crates/chia_streamable_macro/src/lib.rs` with JSON dictionary values at a fork-height or boundary-value activation point make chia_rs produce a Rust/Python disagreement, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:234` / `update_digest`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `update_digest` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
