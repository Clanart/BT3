# Q2895: update digest reuse stale verification state via macro-generated vector fields

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with macro-generated vector fields when serialized bytes are validly framed but semantically adversarial make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:200` / `update_digest`
- Entrypoint: parse generated streamable bytes
- Attacker controls: macro-generated vector fields
- Exploit idea: Drive `update_digest` through its public caller path using macro-generated vector fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
