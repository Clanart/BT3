# Q3368: hash to g2 derive a different canonical hash via infinity and subgroup edge cases

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `hash_to_g2` in `crates/chia-bls/src/signature.rs` with infinity and subgroup edge cases when the payload is accepted by one public API before another validates it make chia_rs derive a different canonical hash, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/signature.rs:311` / `hash_to_g2`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `hash_to_g2` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: test cache update/evict paths with message-public-key collisions.
