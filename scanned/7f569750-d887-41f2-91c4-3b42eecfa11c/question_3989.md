# Q3989: Args allow replay across contexts via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `Args` in `crates/clvm-utils/src/hash_encoder.rs` with CLVM atoms with redundant sign bytes when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-utils/src/hash_encoder.rs:91` / `Args`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `Args` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
