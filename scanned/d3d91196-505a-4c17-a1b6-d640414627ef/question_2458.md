# Q2458: check accept invalid consensus data via allocator node pairs and atoms

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `check` in `crates/clvm-utils/src/curried_program.rs` with allocator node pairs and atoms when serialized bytes are validly framed but semantically adversarial make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/curried_program.rs:43` / `check`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `check` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
