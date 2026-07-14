# Q3224: new derive a different canonical hash via infinity and subgroup edge cases

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `new` in `crates/chia-bls/src/bls_cache.rs` with infinity and subgroup edge cases when the attacker can choose ordering inside a batch make chia_rs derive a different canonical hash, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/bls_cache.rs:63` / `new`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `new` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
