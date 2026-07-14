# Q1731: eq reuse stale verification state via infinity and subgroup edge cases

## Question
Can an unprivileged attacker provide serialized public keys/signatures targeting `eq` in `crates/chia-bls/src/gtelement.rs` with infinity and subgroup edge cases when the payload is accepted by one public API before another validates it make chia_rs reuse stale verification state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/gtelement.rs:52` / `eq`
- Entrypoint: provide serialized public keys/signatures
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `eq` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: test cache update/evict paths with message-public-key collisions.
