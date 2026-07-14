# Q552: v1 omits ref list commit output after an error path via unfinished block payloads

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `v1_omits_ref_list` in `crates/chia-protocol/src/unfinished_block.rs` with unfinished block payloads with default-enabled consensus flags make chia_rs commit output after an error path, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/unfinished_block.rs:416` / `v1_omits_ref_list`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `v1_omits_ref_list` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Rust and Python object construction from the same bytes.
