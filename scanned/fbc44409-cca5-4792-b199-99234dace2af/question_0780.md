# Q780: VDFProof commit output after an error path via partial proof quality strings

## Question
Can an unprivileged attacker validate plot/VDF/weight proof inputs targeting `VDFProof` in `crates/chia-protocol/src/vdf.rs` with partial proof quality strings with default-enabled consensus flags make chia_rs commit output after an error path, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/vdf.rs:14` / `VDFProof`
- Entrypoint: validate plot/VDF/weight proof inputs
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `VDFProof` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare quality string outputs across Rust and Python bindings.
