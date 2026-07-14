# Q1553: assert not ephemeral overflow or underflow a boundary check via coin announcements and puzzle announcements with collidi

## Question
Can an unprivileged attacker include a spend in a block generator targeting `assert_not_ephemeral` in `crates/chia-consensus/src/conditions.rs` with coin announcements and puzzle announcements with colliding payloads when the same payload is parsed through public bindings make chia_rs overflow or underflow a boundary check, violating the invariant that amounts and coin ids remain canonical after sanitization, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:1057` / `assert_not_ephemeral`
- Entrypoint: include a spend in a block generator
- Attacker controls: coin announcements and puzzle announcements with colliding payloads
- Exploit idea: Drive `assert_not_ephemeral` through its public caller path using coin announcements and puzzle announcements with colliding payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: amounts and coin ids remain canonical after sanitization
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip through py_validate_clvm_and_signature and Rust validation and compare results.
