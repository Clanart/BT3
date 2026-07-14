# Q2092: update digest collapse distinct inputs into one accepted state via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker compare trusted and untrusted parse modes targeting `update_digest` in `crates/chia-protocol/src/bytes.rs` with trusted vs untrusted parse mode inputs at a fork-height or boundary-value activation point make chia_rs collapse distinct inputs into one accepted state, violating the invariant that JSON conversions cannot create impossible protocol states, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:85` / `update_digest`
- Entrypoint: compare trusted and untrusted parse modes
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `update_digest` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: JSON conversions cannot create impossible protocol states
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trailing consensus bytes never produce a valid object.
