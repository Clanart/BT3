# Q21: check agg sig unsafe message treat malformed data as a valid empty/default value via negative or oversized condition int

## Question
Can an unprivileged attacker call the Python validation API with attacker-controlled spends targeting `check_agg_sig_unsafe_message` in `crates/chia-consensus/src/conditions.rs` with negative or oversized condition integers at a fork-height or boundary-value activation point make chia_rs treat malformed data as a valid empty/default value, violating the invariant that amounts and coin ids remain canonical after sanitization, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:338` / `check_agg_sig_unsafe_message`
- Entrypoint: call the Python validation API with attacker-controlled spends
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `check_agg_sig_unsafe_message` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: amounts and coin ids remain canonical after sanitization
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: build a generator with the condition variant and assert the exact ErrorCode or accepted SpendBundleConditions.
