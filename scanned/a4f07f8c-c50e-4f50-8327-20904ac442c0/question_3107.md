# Q3107: from produce a Rust/Python disagreement via malformed CLVM condition atoms

## Question
Can an unprivileged attacker feed a malicious CLVM spend output into condition parsing targeting `from` in `crates/chia-consensus/src/owned_conditions.rs` with malformed CLVM condition atoms when serialized bytes are validly framed but semantically adversarial make chia_rs produce a Rust/Python disagreement, violating the invariant that duplicate or malformed conditions cannot relax timelocks or signatures, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/owned_conditions.rs:87` / `from`
- Entrypoint: feed a malicious CLVM spend output into condition parsing
- Attacker controls: malformed CLVM condition atoms
- Exploit idea: Drive `from` through its public caller path using malformed CLVM condition atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: duplicate or malformed conditions cannot relax timelocks or signatures
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip through py_validate_clvm_and_signature and Rust validation and compare results.
