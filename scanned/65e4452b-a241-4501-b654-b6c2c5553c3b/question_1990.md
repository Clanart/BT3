# Q1990: py prev header hash accept invalid consensus data via reward-chain and foliage fields

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `py_prev_header_hash` in `crates/chia-protocol/src/header_block.rs` with reward-chain and foliage fields at a fork-height or boundary-value activation point make chia_rs accept invalid consensus data, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/header_block.rs:100` / `py_prev_header_hash`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: reward-chain and foliage fields
- Exploit idea: Drive `py_prev_header_hash` through its public caller path using reward-chain and foliage fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Rust and Python object construction from the same bytes.
