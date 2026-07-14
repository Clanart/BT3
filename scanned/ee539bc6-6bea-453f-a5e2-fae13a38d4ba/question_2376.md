# Q2376: encode bigint skip a required validation guard via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `encode_bigint` in `crates/clvm-traits/src/clvm_encoder.rs` with CLVM atoms with redundant sign bytes when values sit exactly at max/min integer boundaries make chia_rs skip a required validation guard, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-traits/src/clvm_encoder.rs:67` / `encode_bigint`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `encode_bigint` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
