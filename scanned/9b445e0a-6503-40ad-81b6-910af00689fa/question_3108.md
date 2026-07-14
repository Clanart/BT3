# Q3108: from reuse stale verification state via duplicate and contradictory ASSERT * conditions

## Question
Can an unprivileged attacker feed a malicious CLVM spend output into condition parsing targeting `from` in `crates/chia-consensus/src/owned_conditions.rs` with duplicate and contradictory ASSERT_* conditions when serialized bytes are validly framed but semantically adversarial make chia_rs reuse stale verification state, violating the invariant that duplicate or malformed conditions cannot relax timelocks or signatures, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/owned_conditions.rs:144` / `from`
- Entrypoint: feed a malicious CLVM spend output into condition parsing
- Attacker controls: duplicate and contradictory ASSERT_* conditions
- Exploit idea: Drive `from` through its public caller path using duplicate and contradictory ASSERT_* conditions; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: duplicate or malformed conditions cannot relax timelocks or signatures
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip through py_validate_clvm_and_signature and Rust validation and compare results.
