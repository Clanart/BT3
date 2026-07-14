# Q2303: update digest derive a different canonical hash via partial proof quality strings

## Question
Can an unprivileged attacker submit proof and block challenge data targeting `update_digest` in `crates/chia-protocol/src/weight_proof.rs` with partial proof quality strings with default-enabled consensus flags make chia_rs derive a different canonical hash, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/weight_proof.rs:23` / `update_digest`
- Entrypoint: submit proof and block challenge data
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `update_digest` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: test boundary iteration values against a simple arithmetic model.
