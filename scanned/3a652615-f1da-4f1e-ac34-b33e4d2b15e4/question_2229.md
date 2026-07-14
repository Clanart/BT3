# Q2229: RegisterForPhUpdates commit output after an error path via JSON dict conversion values

## Question
Can an unprivileged attacker relay network payload bytes through streamable decoding targeting `RegisterForPhUpdates` in `crates/chia-protocol/src/wallet_protocol.rs` with JSON dict conversion values when duplicate or prefix-colliding items are present make chia_rs commit output after an error path, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/wallet_protocol.rs:153` / `RegisterForPhUpdates`
- Entrypoint: relay network payload bytes through streamable decoding
- Attacker controls: JSON dict conversion values
- Exploit idea: Drive `RegisterForPhUpdates` through its public caller path using JSON dict conversion values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test JSON dict conversion against streamable bytes.
