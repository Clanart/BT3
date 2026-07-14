# Q2161: NewPeak mis-bind attacker-controlled bytes to trusted state via sized integer boundary values

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `NewPeak` in `crates/chia-protocol/src/full_node_protocol.rs` with sized integer boundary values when a node processes data from an untrusted peer or wallet make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that integer and length encodings cannot change hashes across implementations, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/full_node_protocol.rs:14` / `NewPeak`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: sized integer boundary values
- Exploit idea: Drive `NewPeak` through its public caller path using sized integer boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: integer and length encodings cannot change hashes across implementations
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes through parse/stream/hash in trusted and untrusted modes.
