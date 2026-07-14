# Q421: update digest accept invalid consensus data via serialized CoinSpend and SpendBundle objects

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `update_digest` in `crates/chia-protocol/src/fullblock.rs` with serialized CoinSpend and SpendBundle objects when the payload is accepted by one public API before another validates it make chia_rs accept invalid consensus data, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/fullblock.rs:46` / `update_digest`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: serialized CoinSpend and SpendBundle objects
- Exploit idea: Drive `update_digest` through its public caller path using serialized CoinSpend and SpendBundle objects; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: parse-stream-hash round-trip the object and compare field hashes.
