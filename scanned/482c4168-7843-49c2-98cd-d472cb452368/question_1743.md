# Q1743: from json dict reuse stale verification state via infinity and subgroup edge cases

## Question
Can an unprivileged attacker submit aggregate signature material targeting `from_json_dict` in `crates/chia-bls/src/gtelement.rs` with infinity and subgroup edge cases when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/gtelement.rs:156` / `from_json_dict`
- Entrypoint: submit aggregate signature material
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `from_json_dict` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: compare aggregate_verify with independent pairings.
