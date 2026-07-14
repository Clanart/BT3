# Q855: encode bigint skip a required validation guard via curried program argument trees

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `encode_bigint` in `crates/clvm-traits/src/clvm_encoder.rs` with curried program argument trees when a node processes data from an untrusted peer or wallet make chia_rs skip a required validation guard, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/clvm_encoder.rs:67` / `encode_bigint`
- Entrypoint: hash curried CLVM programs
- Attacker controls: curried program argument trees
- Exploit idea: Drive `encode_bigint` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: differential-test curried tree hash against executing the curried program.
