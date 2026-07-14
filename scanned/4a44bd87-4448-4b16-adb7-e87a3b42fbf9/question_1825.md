# Q1825: from uncompressed mis-bind attacker-controlled bytes to trusted state via aggregate signature participant lists

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `from_uncompressed` in `crates/chia-bls/src/signature.rs` with aggregate signature participant lists when values sit exactly at max/min integer boundaries make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/signature.rs:62` / `from_uncompressed`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: aggregate signature participant lists
- Exploit idea: Drive `from_uncompressed` through its public caller path using aggregate signature participant lists; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: compare aggregate_verify with independent pairings.
