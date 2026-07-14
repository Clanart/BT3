# Q2204: update digest allow replay across contexts via list and vector length fields

## Question
Can an unprivileged attacker compare trusted and untrusted parse modes targeting `update_digest` in `crates/chia-protocol/src/utils.rs` with list and vector length fields at a fork-height or boundary-value activation point make chia_rs allow replay across contexts, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/utils.rs:5` / `update_digest`
- Entrypoint: compare trusted and untrusted parse modes
- Attacker controls: list and vector length fields
- Exploit idea: Drive `update_digest` through its public caller path using list and vector length fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes through parse/stream/hash in trusted and untrusted modes.
