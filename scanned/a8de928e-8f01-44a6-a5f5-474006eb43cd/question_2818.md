# Q2818: get memo accept invalid consensus data via PyO3 object extraction values

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `get_memo` in `wheel/src/api.rs` with PyO3 object extraction values when a node processes data from an untrusted peer or wallet make chia_rs accept invalid consensus data, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:691` / `get_memo`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `get_memo` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
