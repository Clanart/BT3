# Q3802: make v2 mis-bind attacker-controlled bytes to trusted state via partial proof quality strings

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `make_v2` in `crates/chia-protocol/src/proof_of_space.rs` with partial proof quality strings when a node processes data from an untrusted peer or wallet make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that invalid proofs cannot produce valid quality strings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:67` / `make_v2`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `make_v2` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid proofs cannot produce valid quality strings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
