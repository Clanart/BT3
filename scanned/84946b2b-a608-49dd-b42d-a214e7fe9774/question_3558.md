# Q3558: update digest commit output after an error path via FullBlock/HeaderBlock byte streams

## Question
Can an unprivileged attacker parse and relay serialized protocol objects targeting `update_digest` in `crates/chia-protocol/src/reward_chain_block.rs` with FullBlock/HeaderBlock byte streams when the attacker can choose ordering inside a batch make chia_rs commit output after an error path, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/reward_chain_block.rs:48` / `update_digest`
- Entrypoint: parse and relay serialized protocol objects
- Attacker controls: FullBlock/HeaderBlock byte streams
- Exploit idea: Drive `update_digest` through its public caller path using FullBlock/HeaderBlock byte streams; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate each serialized field and assert hash or validation changes.
