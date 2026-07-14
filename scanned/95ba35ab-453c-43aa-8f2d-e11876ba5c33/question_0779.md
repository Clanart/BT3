# Q779: VDFInfo allow replay across contexts via overflow block signage point values

## Question
Can an unprivileged attacker validate plot/VDF/weight proof inputs targeting `VDFInfo` in `crates/chia-protocol/src/vdf.rs` with overflow block signage point values with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/vdf.rs:7` / `VDFInfo`
- Entrypoint: validate plot/VDF/weight proof inputs
- Attacker controls: overflow block signage point values
- Exploit idea: Drive `VDFInfo` through its public caller path using overflow block signage point values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare quality string outputs across Rust and Python bindings.
