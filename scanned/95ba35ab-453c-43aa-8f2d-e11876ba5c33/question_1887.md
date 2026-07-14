# Q1887: from bytes reuse stale verification state via infinity and subgroup edge cases

## Question
Can an unprivileged attacker submit aggregate signature material targeting `from_bytes` in `crates/chia-secp/src/secp256k1/signature.rs` with infinity and subgroup edge cases when the same payload is parsed through public bindings make chia_rs reuse stale verification state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-secp/src/secp256k1/signature.rs:43` / `from_bytes`
- Entrypoint: submit aggregate signature material
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `from_bytes` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
