# Q2264: PartialProof allow replay across contexts via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker submit proof and block challenge data targeting `PartialProof` in `crates/chia-protocol/src/partial_proof.rs` with weight proof summaries and sub-epoch data when values sit exactly at max/min integer boundaries make chia_rs allow replay across contexts, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:9` / `PartialProof`
- Entrypoint: submit proof and block challenge data
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `PartialProof` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: test boundary iteration values against a simple arithmetic model.
