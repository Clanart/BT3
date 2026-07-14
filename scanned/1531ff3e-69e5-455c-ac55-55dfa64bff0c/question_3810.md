# Q3810: py quality string commit output after an error path via VDF/classgroup byte encodings

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `py_quality_string` in `crates/chia-protocol/src/proof_of_space.rs` with VDF/classgroup byte encodings when the payload is accepted by one public API before another validates it make chia_rs commit output after an error path, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:211` / `py_quality_string`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: VDF/classgroup byte encodings
- Exploit idea: Drive `py_quality_string` through its public caller path using VDF/classgroup byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare quality string outputs across Rust and Python bindings.
