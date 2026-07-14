# Q3247: from accept invalid consensus data via duplicate public-key/message pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `from` in `crates/chia-bls/src/error.rs` with duplicate public-key/message pairs when a node processes data from an untrusted peer or wallet make chia_rs accept invalid consensus data, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/error.rs:23` / `from`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: duplicate public-key/message pairs
- Exploit idea: Drive `from` through its public caller path using duplicate public-key/message pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: test cache update/evict paths with message-public-key collisions.
