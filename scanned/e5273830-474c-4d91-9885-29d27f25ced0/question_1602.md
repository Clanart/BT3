# Q1602: convert block to bundle treat malformed data as a valid empty/default value via malformed CLVM condition atoms

## Question
Can an unprivileged attacker include a spend in a block generator targeting `convert_block_to_bundle` in `crates/chia-consensus/src/spendbundle_conditions.rs` with malformed CLVM condition atoms when values sit exactly at max/min integer boundaries make chia_rs treat malformed data as a valid empty/default value, violating the invariant that amounts and coin ids remain canonical after sanitization, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/spendbundle_conditions.rs:486` / `convert_block_to_bundle`
- Entrypoint: include a spend in a block generator
- Attacker controls: malformed CLVM condition atoms
- Exploit idea: Drive `convert_block_to_bundle` through its public caller path using malformed CLVM condition atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: amounts and coin ids remain canonical after sanitization
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test mempool flags versus block flags for the same spend.
