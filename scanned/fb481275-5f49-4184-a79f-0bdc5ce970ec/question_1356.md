# Q1356: from bytes commit output after an error path via newtype and enum field encodings

## Question
Can an unprivileged attacker compute streamable hashes targeting `from_bytes` in `crates/chia-traits/src/streamable.rs` with newtype and enum field encodings when duplicate or prefix-colliding items are present make chia_rs commit output after an error path, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:45` / `from_bytes`
- Entrypoint: compute streamable hashes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `from_bytes` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
