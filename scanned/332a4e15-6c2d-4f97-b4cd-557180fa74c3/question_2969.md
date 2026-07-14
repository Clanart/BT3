# Q2969: update digest overflow or underflow a boundary check via newtype and enum field encodings

## Question
Can an unprivileged attacker compute streamable hashes targeting `update_digest` in `crates/chia_streamable_macro/src/lib.rs` with newtype and enum field encodings with default-enabled consensus flags make chia_rs overflow or underflow a boundary check, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_streamable_macro/src/lib.rs:250` / `update_digest`
- Entrypoint: compute streamable hashes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `update_digest` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
