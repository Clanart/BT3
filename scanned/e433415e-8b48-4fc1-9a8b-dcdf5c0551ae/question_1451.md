# Q1451: update digest allow replay across contexts via JSON dictionary values

## Question
Can an unprivileged attacker compute streamable hashes targeting `update_digest` in `crates/chia_streamable_macro/src/lib.rs` with JSON dictionary values at a fork-height or boundary-value activation point make chia_rs allow replay across contexts, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:267` / `update_digest`
- Entrypoint: compute streamable hashes
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `update_digest` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
