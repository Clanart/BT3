# Q2927: hash derive a different canonical hash via newtype and enum field encodings

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `__hash__` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when a node processes data from an untrusted peer or wallet make chia_rs derive a different canonical hash, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:101` / `__hash__`
- Entrypoint: parse generated streamable bytes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `__hash__` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
