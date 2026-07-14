# Q782: update digest derive a different canonical hash via VDF/classgroup byte encodings

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `update_digest` in `crates/chia-protocol/src/weight_proof.rs` with VDF/classgroup byte encodings at a fork-height or boundary-value activation point make chia_rs derive a different canonical hash, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/weight_proof.rs:23` / `update_digest`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: VDF/classgroup byte encodings
- Exploit idea: Drive `update_digest` through its public caller path using VDF/classgroup byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
