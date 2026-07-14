# Q3288: neg reuse stale verification state via aggregate signature participant lists

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `neg` in `crates/chia-bls/src/public_key.rs` with aggregate signature participant lists with default-enabled consensus flags make chia_rs reuse stale verification state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:225` / `neg`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: aggregate signature participant lists
- Exploit idea: Drive `neg` through its public caller path using aggregate signature participant lists; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: test cache update/evict paths with message-public-key collisions.
