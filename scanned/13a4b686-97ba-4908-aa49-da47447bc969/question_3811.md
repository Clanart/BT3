# Q3811: update digest accept invalid consensus data via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `update_digest` in `crates/chia-protocol/src/proof_of_space.rs` with weight proof summaries and sub-epoch data when the payload is accepted by one public API before another validates it make chia_rs accept invalid consensus data, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:227` / `update_digest`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `update_digest` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
