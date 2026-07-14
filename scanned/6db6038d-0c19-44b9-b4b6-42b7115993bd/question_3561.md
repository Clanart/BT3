# Q3561: get unfinished skip a required validation guard via reward-chain and foliage fields

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `get_unfinished` in `crates/chia-protocol/src/reward_chain_block.rs` with reward-chain and foliage fields when the attacker can choose ordering inside a batch make chia_rs skip a required validation guard, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/reward_chain_block.rs:130` / `get_unfinished`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: reward-chain and foliage fields
- Exploit idea: Drive `get_unfinished` through its public caller path using reward-chain and foliage fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Rust and Python object construction from the same bytes.
