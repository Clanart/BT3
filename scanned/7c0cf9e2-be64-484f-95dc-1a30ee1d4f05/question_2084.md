# Q2084: new allow replay across contexts via list and vector length fields

## Question
Can an unprivileged attacker compare trusted and untrusted parse modes targeting `new` in `crates/chia-protocol/src/bytes.rs` with list and vector length fields with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:27` / `new`
- Entrypoint: compare trusted and untrusted parse modes
- Attacker controls: list and vector length fields
- Exploit idea: Drive `new` through its public caller path using list and vector length fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip bytes through parse/stream/hash in trusted and untrusted modes.
