# Q2799: fast forward singleton reuse stale verification state via run generator API arguments

## Question
Can an unprivileged attacker call the public Python API targeting `fast_forward_singleton` in `wheel/src/api.rs` with run_generator API arguments when the attacker can choose ordering inside a batch make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:436` / `fast_forward_singleton`
- Entrypoint: call the public Python API
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `fast_forward_singleton` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
