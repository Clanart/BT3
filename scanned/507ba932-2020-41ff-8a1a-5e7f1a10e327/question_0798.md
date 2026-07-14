# Q798: check rest value reuse stale verification state via big integer encodings

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `check_rest_value` in `crates/clvm-derive/src/from_clvm.rs` with big integer encodings when the same payload is parsed through public bindings make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-derive/src/from_clvm.rs:150` / `check_rest_value`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: big integer encodings
- Exploit idea: Drive `check_rest_value` through its public caller path using big integer encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
