# Q3809: py compute plot id allow replay across contexts via proof-of-space challenge/proof bytes

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `py_compute_plot_id` in `crates/chia-protocol/src/proof_of_space.rs` with proof-of-space challenge/proof bytes when the payload is accepted by one public API before another validates it make chia_rs allow replay across contexts, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:206` / `py_compute_plot_id`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: proof-of-space challenge/proof bytes
- Exploit idea: Drive `py_compute_plot_id` through its public caller path using proof-of-space challenge/proof bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare quality string outputs across Rust and Python bindings.
