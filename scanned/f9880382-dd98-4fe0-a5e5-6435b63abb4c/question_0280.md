# Q280: update digest mis-bind attacker-controlled bytes to trusted state via infinity and subgroup edge cases

## Question
Can an unprivileged attacker derive keys from attacker-controlled indexes targeting `update_digest` in `crates/chia-bls/src/secret_key.rs` with infinity and subgroup edge cases when the attacker can choose ordering inside a batch make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/secret_key.rs:165` / `update_digest`
- Entrypoint: derive keys from attacker-controlled indexes
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `update_digest` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: test cache update/evict paths with message-public-key collisions.
