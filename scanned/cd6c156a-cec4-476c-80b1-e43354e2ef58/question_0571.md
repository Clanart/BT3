# Q571: update digest collapse distinct inputs into one accepted state via streamable byte prefixes and trailing bytes

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `update_digest` in `crates/chia-protocol/src/bytes.rs` with streamable byte prefixes and trailing bytes at a fork-height or boundary-value activation point make chia_rs collapse distinct inputs into one accepted state, violating the invariant that JSON conversions cannot create impossible protocol states, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:85` / `update_digest`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: streamable byte prefixes and trailing bytes
- Exploit idea: Drive `update_digest` through its public caller path using streamable byte prefixes and trailing bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: JSON conversions cannot create impossible protocol states
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test JSON dict conversion against streamable bytes.
