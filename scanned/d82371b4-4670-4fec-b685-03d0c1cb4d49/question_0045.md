# Q45: cond test sig treat malformed data as a valid empty/default value via negative or oversized condition integers

## Question
Can an unprivileged attacker call the Python validation API with attacker-controlled spends targeting `cond_test_sig` in `crates/chia-consensus/src/conditions.rs` with negative or oversized condition integers when serialized bytes are validly framed but semantically adversarial make chia_rs treat malformed data as a valid empty/default value, violating the invariant that amounts and coin ids remain canonical after sanitization, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:2063` / `cond_test_sig`
- Entrypoint: call the Python validation API with attacker-controlled spends
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `cond_test_sig` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: amounts and coin ids remain canonical after sanitization
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: build a generator with the condition variant and assert the exact ErrorCode or accepted SpendBundleConditions.
