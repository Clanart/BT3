# Q3722: update digest overflow or underflow a boundary check via JSON dict conversion values

## Question
Can an unprivileged attacker compare trusted and untrusted parse modes targeting `update_digest` in `crates/chia-protocol/src/sub_epoch_summary.rs` with JSON dict conversion values with default-enabled consensus flags make chia_rs overflow or underflow a boundary check, violating the invariant that JSON conversions cannot create impossible protocol states, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/sub_epoch_summary.rs:21` / `update_digest`
- Entrypoint: compare trusted and untrusted parse modes
- Attacker controls: JSON dict conversion values
- Exploit idea: Drive `update_digest` through its public caller path using JSON dict conversion values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: JSON conversions cannot create impossible protocol states
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test JSON dict conversion against streamable bytes.
