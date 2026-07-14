# Q2308: is challenge collapse distinct inputs into one accepted state via overflow block signage point values

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `is_challenge` in `crates/chia-protocol/src/weight_proof.rs` with overflow block signage point values with default-enabled consensus flags make chia_rs collapse distinct inputs into one accepted state, violating the invariant that overflow block decisions are consistent at boundaries, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/weight_proof.rs:96` / `is_challenge`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: overflow block signage point values
- Exploit idea: Drive `is_challenge` through its public caller path using overflow block signage point values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: overflow block decisions are consistent at boundaries
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
