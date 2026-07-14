# Q3035: Sha256 derive a different canonical hash via caller-provided buffers

## Question
Can an unprivileged attacker batch repeated API calls targeting `Sha256` in `crates/chia-sha2/src/lib.rs` with caller-provided buffers when values sit exactly at max/min integer boundaries make chia_rs derive a different canonical hash, violating the invariant that public API outputs remain deterministic for identical inputs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-sha2/src/lib.rs:5` / `Sha256`
- Entrypoint: batch repeated API calls
- Attacker controls: caller-provided buffers
- Exploit idea: Drive `Sha256` through its public caller path using caller-provided buffers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: public API outputs remain deterministic for identical inputs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare outputs across Rust/Python wrappers for identical bytes.
