# Q3985: TreeHasher collapse distinct inputs into one accepted state via curried program argument trees

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `TreeHasher` in `crates/clvm-utils/src/hash_encoder.rs` with curried program argument trees when serialized bytes are validly framed but semantically adversarial make chia_rs collapse distinct inputs into one accepted state, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/hash_encoder.rs:22` / `TreeHasher`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: curried program argument trees
- Exploit idea: Drive `TreeHasher` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
