# Q593: update digest produce a Rust/Python disagreement via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker parse untrusted streamable bytes targeting `update_digest` in `crates/chia-protocol/src/bytes.rs` with trusted vs untrusted parse mode inputs when duplicate or prefix-colliding items are present make chia_rs produce a Rust/Python disagreement, violating the invariant that integer and length encodings cannot change hashes across implementations, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:257` / `update_digest`
- Entrypoint: parse untrusted streamable bytes
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `update_digest` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: integer and length encodings cannot change hashes across implementations
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test JSON dict conversion against streamable bytes.
