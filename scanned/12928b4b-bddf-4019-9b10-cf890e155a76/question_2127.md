# Q2127: from reuse stale verification state via JSON dict conversion values

## Question
Can an unprivileged attacker parse untrusted streamable bytes targeting `from` in `crates/chia-protocol/src/bytes.rs` with JSON dict conversion values when serialized bytes are validly framed but semantically adversarial make chia_rs reuse stale verification state, violating the invariant that integer and length encodings cannot change hashes across implementations, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:367` / `from`
- Entrypoint: parse untrusted streamable bytes
- Attacker controls: JSON dict conversion values
- Exploit idea: Drive `from` through its public caller path using JSON dict conversion values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: integer and length encodings cannot change hashes across implementations
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test JSON dict conversion against streamable bytes.
