# Q795: from clvm skip a required validation guard via curried program argument trees

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `from_clvm` in `crates/clvm-derive/src/from_clvm.rs` with curried program argument trees when the same payload is parsed through public bindings make chia_rs skip a required validation guard, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-derive/src/from_clvm.rs:11` / `from_clvm`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: curried program argument trees
- Exploit idea: Drive `from_clvm` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: differential-test curried tree hash against executing the curried program.
