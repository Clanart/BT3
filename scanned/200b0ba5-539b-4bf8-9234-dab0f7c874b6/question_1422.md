# Q1422: py to bytes reuse stale verification state via newtype and enum field encodings

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `py_to_bytes` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:372` / `py_to_bytes`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `py_to_bytes` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
