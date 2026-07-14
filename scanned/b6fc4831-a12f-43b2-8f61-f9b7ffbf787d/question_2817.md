# Q2817: get filename commit output after an error path via run generator API arguments

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `get_filename` in `wheel/src/api.rs` with run_generator API arguments when a node processes data from an untrusted peer or wallet make chia_rs commit output after an error path, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:687` / `get_filename`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `get_filename` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
