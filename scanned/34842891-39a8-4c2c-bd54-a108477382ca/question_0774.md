# Q774: make pos reuse stale verification state via partial proof quality strings

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `make_pos` in `crates/chia-protocol/src/proof_of_space.rs` with partial proof quality strings with default-enabled consensus flags make chia_rs reuse stale verification state, violating the invariant that invalid proofs cannot produce valid quality strings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:365` / `make_pos`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `make_pos` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid proofs cannot produce valid quality strings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
