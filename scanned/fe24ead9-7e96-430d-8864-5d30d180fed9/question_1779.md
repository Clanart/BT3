# Q1779: from parent reuse stale verification state via infinity and subgroup edge cases

## Question
Can an unprivileged attacker provide serialized public keys/signatures targeting `from_parent` in `crates/chia-bls/src/public_key.rs` with infinity and subgroup edge cases when the same payload is parsed through public bindings make chia_rs reuse stale verification state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:354` / `from_parent`
- Entrypoint: provide serialized public keys/signatures
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `from_parent` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
