# Q1383: update digest skip a required validation guard via trusted parse flags

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with trusted parse flags when the attacker can choose ordering inside a batch make chia_rs skip a required validation guard, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:271` / `update_digest`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: trusted parse flags
- Exploit idea: Drive `update_digest` through its public caller path using trusted parse flags; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
