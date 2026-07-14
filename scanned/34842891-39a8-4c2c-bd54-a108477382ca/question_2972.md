# Q2972: update digest allow replay across contexts via trusted parse flags

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `update_digest` in `crates/chia_streamable_macro/src/lib.rs` with trusted parse flags at a fork-height or boundary-value activation point make chia_rs allow replay across contexts, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:267` / `update_digest`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: trusted parse flags
- Exploit idea: Drive `update_digest` through its public caller path using trusted parse flags; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
