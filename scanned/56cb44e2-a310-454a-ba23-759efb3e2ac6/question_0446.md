# Q446: make v1 block derive a different canonical hash via FullBlock/HeaderBlock byte streams

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `make_v1_block` in `crates/chia-protocol/src/fullblock.rs` with FullBlock/HeaderBlock byte streams with default-enabled consensus flags make chia_rs derive a different canonical hash, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/fullblock.rs:383` / `make_v1_block`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: FullBlock/HeaderBlock byte streams
- Exploit idea: Drive `make_v1_block` through its public caller path using FullBlock/HeaderBlock byte streams; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate each serialized field and assert hash or validation changes.
