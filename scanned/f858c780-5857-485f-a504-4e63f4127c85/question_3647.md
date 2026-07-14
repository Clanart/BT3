# Q3647: from produce a Rust/Python disagreement via streamable byte prefixes and trailing bytes

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `from` in `crates/chia-protocol/src/bytes.rs` with streamable byte prefixes and trailing bytes when duplicate or prefix-colliding items are present make chia_rs produce a Rust/Python disagreement, violating the invariant that integer and length encodings cannot change hashes across implementations, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:361` / `from`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: streamable byte prefixes and trailing bytes
- Exploit idea: Drive `from` through its public caller path using streamable byte prefixes and trailing bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: integer and length encodings cannot change hashes across implementations
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trailing consensus bytes never produce a valid object.
