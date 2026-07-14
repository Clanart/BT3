# Q882: encode number reuse stale verification state via big integer encodings

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `encode_number` in `crates/clvm-traits/src/int_encoding.rs` with big integer encodings with default-enabled consensus flags make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/int_encoding.rs:3` / `encode_number`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: big integer encodings
- Exploit idea: Drive `encode_number` through its public caller path using big integer encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
