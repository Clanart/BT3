# Q2978: from produce a Rust/Python disagreement via block record and sub-epoch edge values

## Question
Can an unprivileged attacker validate a spend under attacker-chosen block context targeting `from` in `crates/chia-consensus/src/error.rs` with block record and sub-epoch edge values at a fork-height or boundary-value activation point make chia_rs produce a Rust/Python disagreement, violating the invariant that time and height context cannot be bypassed, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/error.rs:56` / `from`
- Entrypoint: validate a spend under attacker-chosen block context
- Attacker controls: block record and sub-epoch edge values
- Exploit idea: Drive `from` through its public caller path using block record and sub-epoch edge values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: time and height context cannot be bypassed
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: run boundary-height fixtures under adjacent flags and assert expected fork behavior.
