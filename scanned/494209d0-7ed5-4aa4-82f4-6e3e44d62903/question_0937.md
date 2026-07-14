# Q937: check accept invalid consensus data via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `check` in `crates/clvm-utils/src/curried_program.rs` with CLVM atoms with redundant sign bytes when the attacker can choose ordering inside a batch make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/curried_program.rs:43` / `check`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `check` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
