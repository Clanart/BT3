# Q1316: run chia program overflow or underflow a boundary check via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `run_chia_program` in `wheel/src/run_program.rs` with Python lists of tuple spend inputs when equivalent-looking encodings are mixed make chia_rs overflow or underflow a boundary check, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/run_program.rs:36` / `run_chia_program`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `run_chia_program` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
