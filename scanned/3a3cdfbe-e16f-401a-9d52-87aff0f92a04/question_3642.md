# Q3642: try from commit output after an error path via sized integer boundary values

## Question
Can an unprivileged attacker compare trusted and untrusted parse modes targeting `try_from` in `crates/chia-protocol/src/bytes.rs` with sized integer boundary values when duplicate or prefix-colliding items are present make chia_rs commit output after an error path, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:323` / `try_from`
- Entrypoint: compare trusted and untrusted parse modes
- Attacker controls: sized integer boundary values
- Exploit idea: Drive `try_from` through its public caller path using sized integer boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test JSON dict conversion against streamable bytes.
