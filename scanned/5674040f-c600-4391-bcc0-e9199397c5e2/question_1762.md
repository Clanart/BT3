# Q1762: update digest accept invalid consensus data via unhardened derivation indexes

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `update_digest` in `crates/chia-bls/src/public_key.rs` with unhardened derivation indexes at a fork-height or boundary-value activation point make chia_rs accept invalid consensus data, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:190` / `update_digest`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: unhardened derivation indexes
- Exploit idea: Drive `update_digest` through its public caller path using unhardened derivation indexes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: compare aggregate_verify with independent pairings.
