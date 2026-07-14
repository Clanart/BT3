# Q1735: update digest mis-order operations across a batch via aggregate signature participant lists

## Question
Can an unprivileged attacker submit aggregate signature material targeting `update_digest` in `crates/chia-bls/src/gtelement.rs` with aggregate signature participant lists when the payload is accepted by one public API before another validates it make chia_rs mis-order operations across a batch, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/gtelement.rs:91` / `update_digest`
- Entrypoint: submit aggregate signature material
- Attacker controls: aggregate signature participant lists
- Exploit idea: Drive `update_digest` through its public caller path using aggregate signature participant lists; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: test cache update/evict paths with message-public-key collisions.
