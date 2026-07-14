# Q3832: RecentChainData mis-order operations across a batch via partial proof quality strings

## Question
Can an unprivileged attacker validate plot/VDF/weight proof inputs targeting `RecentChainData` in `crates/chia-protocol/src/weight_proof.rs` with partial proof quality strings with default-enabled consensus flags make chia_rs mis-order operations across a batch, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/weight_proof.rs:115` / `RecentChainData`
- Entrypoint: validate plot/VDF/weight proof inputs
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `RecentChainData` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
