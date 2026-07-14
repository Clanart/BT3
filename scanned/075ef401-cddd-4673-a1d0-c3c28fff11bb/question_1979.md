# Q1979: HeaderBlock derive a different canonical hash via unfinished block payloads

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `HeaderBlock` in `crates/chia-protocol/src/header_block.rs` with unfinished block payloads with default-enabled consensus flags make chia_rs derive a different canonical hash, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/header_block.rs:13` / `HeaderBlock`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `HeaderBlock` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: parse-stream-hash round-trip the object and compare field hashes.
