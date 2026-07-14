# Q3290: sub assign overflow or underflow a boundary check via infinity and subgroup edge cases

## Question
Can an unprivileged attacker provide serialized public keys/signatures targeting `sub_assign` in `crates/chia-bls/src/public_key.rs` with infinity and subgroup edge cases at a fork-height or boundary-value activation point make chia_rs overflow or underflow a boundary check, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:241` / `sub_assign`
- Entrypoint: provide serialized public keys/signatures
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `sub_assign` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: test cache update/evict paths with message-public-key collisions.
