# Q959: insert allow replay across contexts via allocator node pairs and atoms

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `insert` in `crates/clvm-utils/src/tree_hash.rs` with allocator node pairs and atoms when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:94` / `insert`
- Entrypoint: hash curried CLVM programs
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `insert` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
