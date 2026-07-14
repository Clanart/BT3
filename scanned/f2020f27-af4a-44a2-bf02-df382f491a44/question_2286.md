# Q2286: quality string treat malformed data as a valid empty/default value via proof-of-space challenge/proof bytes

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `quality_string` in `crates/chia-protocol/src/proof_of_space.rs` with proof-of-space challenge/proof bytes when the payload is accepted by one public API before another validates it make chia_rs treat malformed data as a valid empty/default value, violating the invariant that overflow block decisions are consistent at boundaries, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:162` / `quality_string`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: proof-of-space challenge/proof bytes
- Exploit idea: Drive `quality_string` through its public caller path using proof-of-space challenge/proof bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: overflow block decisions are consistent at boundaries
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
