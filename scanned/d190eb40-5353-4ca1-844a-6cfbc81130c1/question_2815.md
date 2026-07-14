# Q2815: plot id mis-order operations across a batch via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker call the public Python API targeting `plot_id` in `wheel/src/api.rs` with Python lists of tuple spend inputs when values sit exactly at max/min integer boundaries make chia_rs mis-order operations across a batch, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:679` / `plot_id`
- Entrypoint: call the public Python API
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `plot_id` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
