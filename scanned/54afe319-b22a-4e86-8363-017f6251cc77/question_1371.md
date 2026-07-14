# Q1371: update digest skip a required validation guard via trusted parse flags

## Question
Can an unprivileged attacker compute streamable hashes targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with trusted parse flags when serialized bytes are validly framed but semantically adversarial make chia_rs skip a required validation guard, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:190` / `update_digest`
- Entrypoint: compute streamable hashes
- Attacker controls: trusted parse flags
- Exploit idea: Drive `update_digest` through its public caller path using trusted parse flags; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
