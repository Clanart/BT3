# Q1660: calculate generator length collapse distinct inputs into one accepted state via CLVM program cost boundary inputs

## Question
Can an unprivileged attacker build a compressed block from user-controlled spend bundles targeting `calculate_generator_length` in `crates/chia-consensus/src/solution_generator.rs` with CLVM program cost boundary inputs at a fork-height or boundary-value activation point make chia_rs collapse distinct inputs into one accepted state, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/solution_generator.rs:68` / `calculate_generator_length`
- Entrypoint: build a compressed block from user-controlled spend bundles
- Attacker controls: CLVM program cost boundary inputs
- Exploit idea: Drive `calculate_generator_length` through its public caller path using CLVM program cost boundary inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: run both generator paths and compare costs, spends, and errors.
