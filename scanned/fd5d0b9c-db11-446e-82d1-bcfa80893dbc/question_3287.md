# Q3287: neg produce a Rust/Python disagreement via public key and signature byte encodings

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `neg` in `crates/chia-bls/src/public_key.rs` with public key and signature byte encodings with default-enabled consensus flags make chia_rs produce a Rust/Python disagreement, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:217` / `neg`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: public key and signature byte encodings
- Exploit idea: Drive `neg` through its public caller path using public key and signature byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: test cache update/evict paths with message-public-key collisions.
