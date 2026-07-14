# Q3350: is valid overflow or underflow a boundary check via infinity and subgroup edge cases

## Question
Can an unprivileged attacker submit aggregate signature material targeting `is_valid` in `crates/chia-bls/src/signature.rs` with infinity and subgroup edge cases when values sit exactly at max/min integer boundaries make chia_rs overflow or underflow a boundary check, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/signature.rs:94` / `is_valid`
- Entrypoint: submit aggregate signature material
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `is_valid` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: test cache update/evict paths with message-public-key collisions.
