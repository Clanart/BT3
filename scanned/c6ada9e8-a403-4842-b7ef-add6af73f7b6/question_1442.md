# Q1442: update digest derive a different canonical hash via hash/update digest inputs

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `update_digest` in `crates/chia_streamable_macro/src/lib.rs` with hash/update_digest inputs at a fork-height or boundary-value activation point make chia_rs derive a different canonical hash, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:194` / `update_digest`
- Entrypoint: parse generated streamable bytes
- Attacker controls: hash/update_digest inputs
- Exploit idea: Drive `update_digest` through its public caller path using hash/update_digest inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
