# Q2285: compute plot id overflow or underflow a boundary check via partial proof quality strings

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `compute_plot_id` in `crates/chia-protocol/src/proof_of_space.rs` with partial proof quality strings when the payload is accepted by one public API before another validates it make chia_rs overflow or underflow a boundary check, violating the invariant that overflow block decisions are consistent at boundaries, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:137` / `compute_plot_id`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `compute_plot_id` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: overflow block decisions are consistent at boundaries
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: test boundary iteration values against a simple arithmetic model.
