# Q2945: bytes overflow or underflow a boundary check via newtype and enum field encodings

## Question
Can an unprivileged attacker compute streamable hashes targeting `__bytes__` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when the payload is accepted by one public API before another validates it make chia_rs overflow or underflow a boundary check, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:382` / `__bytes__`
- Entrypoint: compute streamable hashes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `__bytes__` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
