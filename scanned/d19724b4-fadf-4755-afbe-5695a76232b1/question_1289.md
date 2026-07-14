# Q1289: create v2 plot produce a Rust/Python disagreement via PyO3 object extraction values

## Question
Can an unprivileged attacker call the public Python API targeting `create_v2_plot` in `wheel/src/api.rs` with PyO3 object extraction values when a node processes data from an untrusted peer or wallet make chia_rs produce a Rust/Python disagreement, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:633` / `create_v2_plot`
- Entrypoint: call the public Python API
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `create_v2_plot` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
