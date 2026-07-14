# Q3091: add signature accept invalid consensus data via negative or oversized condition integers

## Question
Can an unprivileged attacker feed a malicious CLVM spend output into condition parsing targeting `add_signature` in `crates/chia-consensus/src/conditions.rs` with negative or oversized condition integers when the same payload is parsed through public bindings make chia_rs accept invalid consensus data, violating the invariant that only spend conditions satisfying consensus rules are accepted, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:5289` / `add_signature`
- Entrypoint: feed a malicious CLVM spend output into condition parsing
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `add_signature` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: only spend conditions satisfying consensus rules are accepted
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: build a generator with the condition variant and assert the exact ErrorCode or accepted SpendBundleConditions.
