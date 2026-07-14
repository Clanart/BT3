# Q680: update digest overflow or underflow a boundary check via sized integer boundary values

## Question
Can an unprivileged attacker relay network payload bytes through streamable decoding targeting `update_digest` in `crates/chia-protocol/src/sub_epoch_summary.rs` with sized integer boundary values at a fork-height or boundary-value activation point make chia_rs overflow or underflow a boundary check, violating the invariant that JSON conversions cannot create impossible protocol states, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/sub_epoch_summary.rs:21` / `update_digest`
- Entrypoint: relay network payload bytes through streamable decoding
- Attacker controls: sized integer boundary values
- Exploit idea: Drive `update_digest` through its public caller path using sized integer boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: JSON conversions cannot create impossible protocol states
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trailing consensus bytes never produce a valid object.
