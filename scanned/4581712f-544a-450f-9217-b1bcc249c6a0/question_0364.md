# Q364: arbitrary mis-bind attacker-controlled bytes to trusted state via infinity and subgroup edge cases

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `arbitrary` in `crates/chia-secp/src/secp256k1/signature.rs` with infinity and subgroup edge cases when duplicate or prefix-colliding items are present make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-secp/src/secp256k1/signature.rs:31` / `arbitrary`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `arbitrary` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
