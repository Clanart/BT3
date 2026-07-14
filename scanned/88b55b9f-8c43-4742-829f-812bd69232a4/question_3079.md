# Q3079: parse spends accept invalid consensus data via negative or oversized condition integers

## Question
Can an unprivileged attacker include a spend in a block generator targeting `parse_spends` in `crates/chia-consensus/src/conditions.rs` with negative or oversized condition integers at a fork-height or boundary-value activation point make chia_rs accept invalid consensus data, violating the invariant that only spend conditions satisfying consensus rules are accepted, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:1542` / `parse_spends`
- Entrypoint: include a spend in a block generator
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `parse_spends` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: only spend conditions satisfying consensus rules are accepted
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test mempool flags versus block flags for the same spend.
