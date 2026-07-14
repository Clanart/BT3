# Q3111: from parent treat malformed data as a valid empty/default value via CREATE COIN outputs with edge-case amounts and hints

## Question
Can an unprivileged attacker include a spend in a block generator targeting `from_parent` in `crates/chia-consensus/src/owned_conditions.rs` with CREATE_COIN outputs with edge-case amounts and hints when serialized bytes are validly framed but semantically adversarial make chia_rs treat malformed data as a valid empty/default value, violating the invariant that amounts and coin ids remain canonical after sanitization, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-consensus/src/owned_conditions.rs:201` / `from_parent`
- Entrypoint: include a spend in a block generator
- Attacker controls: CREATE_COIN outputs with edge-case amounts and hints
- Exploit idea: Drive `from_parent` through its public caller path using CREATE_COIN outputs with edge-case amounts and hints; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: amounts and coin ids remain canonical after sanitization
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: build a generator with the condition variant and assert the exact ErrorCode or accepted SpendBundleConditions.
