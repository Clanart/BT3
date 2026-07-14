# Q2979: ConsensusFlags reuse stale verification state via mempool-vs-block validation inputs

## Question
Can an unprivileged attacker replay validation with alternate consensus flags targeting `ConsensusFlags` in `crates/chia-consensus/src/flags.rs` with mempool-vs-block validation inputs at a fork-height or boundary-value activation point make chia_rs reuse stale verification state, violating the invariant that time and height context cannot be bypassed, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/flags.rs:16` / `ConsensusFlags`
- Entrypoint: replay validation with alternate consensus flags
- Attacker controls: mempool-vs-block validation inputs
- Exploit idea: Drive `ConsensusFlags` through its public caller path using mempool-vs-block validation inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: time and height context cannot be bypassed
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: run boundary-height fixtures under adjacent flags and assert expected fork behavior.
