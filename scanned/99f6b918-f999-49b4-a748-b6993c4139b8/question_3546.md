# Q3546: update digest commit output after an error path via FullBlock/HeaderBlock byte streams

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `update_digest` in `crates/chia-protocol/src/program.rs` with FullBlock/HeaderBlock byte streams when serialized bytes are validly framed but semantically adversarial make chia_rs commit output after an error path, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/program.rs:431` / `update_digest`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: FullBlock/HeaderBlock byte streams
- Exploit idea: Drive `update_digest` through its public caller path using FullBlock/HeaderBlock byte streams; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
