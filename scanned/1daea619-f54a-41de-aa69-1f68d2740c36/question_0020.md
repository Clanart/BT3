# Q20: Condition overflow or underflow a boundary check via duplicate and contradictory ASSERT * conditions

## Question
Can an unprivileged attacker include a spend in a block generator targeting `Condition` in `crates/chia-consensus/src/conditions.rs` with duplicate and contradictory ASSERT_* conditions at a fork-height or boundary-value activation point make chia_rs overflow or underflow a boundary check, violating the invariant that amounts and coin ids remain canonical after sanitization, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:275` / `Condition`
- Entrypoint: include a spend in a block generator
- Attacker controls: duplicate and contradictory ASSERT_* conditions
- Exploit idea: Drive `Condition` through its public caller path using duplicate and contradictory ASSERT_* conditions; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: amounts and coin ids remain canonical after sanitization
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip through py_validate_clvm_and_signature and Rust validation and compare results.
