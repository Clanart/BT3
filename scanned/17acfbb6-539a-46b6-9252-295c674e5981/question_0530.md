# Q530: update digest derive a different canonical hash via FullBlock/HeaderBlock byte streams

## Question
Can an unprivileged attacker parse and relay serialized protocol objects targeting `update_digest` in `crates/chia-protocol/src/unfinished_block.rs` with FullBlock/HeaderBlock byte streams when the payload is accepted by one public API before another validates it make chia_rs derive a different canonical hash, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/unfinished_block.rs:43` / `update_digest`
- Entrypoint: parse and relay serialized protocol objects
- Attacker controls: FullBlock/HeaderBlock byte streams
- Exploit idea: Drive `update_digest` through its public caller path using FullBlock/HeaderBlock byte streams; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate each serialized field and assert hash or validation changes.
