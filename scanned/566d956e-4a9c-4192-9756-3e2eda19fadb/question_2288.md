# Q2288: py compute plot id allow replay across contexts via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker submit proof and block challenge data targeting `py_compute_plot_id` in `crates/chia-protocol/src/proof_of_space.rs` with weight proof summaries and sub-epoch data when the payload is accepted by one public API before another validates it make chia_rs allow replay across contexts, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:206` / `py_compute_plot_id`
- Entrypoint: submit proof and block challenge data
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `py_compute_plot_id` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
