# Q214: update digest mis-order operations across a batch via infinity and subgroup edge cases

## Question
Can an unprivileged attacker provide serialized public keys/signatures targeting `update_digest` in `crates/chia-bls/src/gtelement.rs` with infinity and subgroup edge cases when equivalent-looking encodings are mixed make chia_rs mis-order operations across a batch, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/gtelement.rs:91` / `update_digest`
- Entrypoint: provide serialized public keys/signatures
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `update_digest` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
