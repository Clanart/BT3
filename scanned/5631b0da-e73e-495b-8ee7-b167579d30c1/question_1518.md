# Q1518: load ca cert reuse stale verification state via caller-provided buffers

## Question
Can an unprivileged attacker batch repeated API calls targeting `load_ca_cert` in `crates/chia-ssl/src/ca.rs` with caller-provided buffers when a node processes data from an untrusted peer or wallet make chia_rs reuse stale verification state, violating the invariant that cross-crate conversions preserve hashes and validation results, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-ssl/src/ca.rs:11` / `load_ca_cert`
- Entrypoint: batch repeated API calls
- Attacker controls: caller-provided buffers
- Exploit idea: Drive `load_ca_cert` through its public caller path using caller-provided buffers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cross-crate conversions preserve hashes and validation results
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare outputs across Rust/Python wrappers for identical bytes.
