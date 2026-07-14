# Q945: encode pair treat malformed data as a valid empty/default value via curried program argument trees

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `encode_pair` in `crates/clvm-utils/src/hash_encoder.rs` with curried program argument trees when the attacker can choose ordering inside a batch make chia_rs treat malformed data as a valid empty/default value, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-utils/src/hash_encoder.rs:31` / `encode_pair`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: curried program argument trees
- Exploit idea: Drive `encode_pair` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
