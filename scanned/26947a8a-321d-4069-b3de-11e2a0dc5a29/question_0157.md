# Q157: ArrayTypes accept invalid consensus data via addition/removal leaf sets with duplicate coin ids

## Question
Can an unprivileged attacker compute a Merkle root from attacker-controlled leaves targeting `ArrayTypes` in `crates/chia-consensus/src/merkle_tree.rs` with addition/removal leaf sets with duplicate coin ids when serialized bytes are validly framed but semantically adversarial make chia_rs accept invalid consensus data, violating the invariant that additions and removals exactly match accepted spends, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:25` / `ArrayTypes`
- Entrypoint: compute a Merkle root from attacker-controlled leaves
- Attacker controls: addition/removal leaf sets with duplicate coin ids
- Exploit idea: Drive `ArrayTypes` through its public caller path using addition/removal leaf sets with duplicate coin ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: additions and removals exactly match accepted spends
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare computed roots before and after sorted/duplicated leaf normalization.
