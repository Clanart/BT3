# Q1428: getstate commit output after an error path via newtype and enum field encodings

## Question
Can an unprivileged attacker compute streamable hashes targeting `__getstate__` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when equivalent-looking encodings are mixed make chia_rs commit output after an error path, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:413` / `__getstate__`
- Entrypoint: compute streamable hashes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `__getstate__` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
