# Q2319: check rest value reuse stale verification state via FromClvm/ToClvm enum discriminants

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `check_rest_value` in `crates/clvm-derive/src/from_clvm.rs` with FromClvm/ToClvm enum discriminants at a fork-height or boundary-value activation point make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-derive/src/from_clvm.rs:150` / `check_rest_value`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: FromClvm/ToClvm enum discriminants
- Exploit idea: Drive `check_rest_value` through its public caller path using FromClvm/ToClvm enum discriminants; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
