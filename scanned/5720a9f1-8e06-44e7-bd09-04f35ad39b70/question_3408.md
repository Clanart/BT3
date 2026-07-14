# Q3408: from bytes reuse stale verification state via aggregate signature participant lists

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `from_bytes` in `crates/chia-secp/src/secp256k1/signature.rs` with aggregate signature participant lists at a fork-height or boundary-value activation point make chia_rs reuse stale verification state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-secp/src/secp256k1/signature.rs:43` / `from_bytes`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: aggregate signature participant lists
- Exploit idea: Drive `from_bytes` through its public caller path using aggregate signature participant lists; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: test cache update/evict paths with message-public-key collisions.
