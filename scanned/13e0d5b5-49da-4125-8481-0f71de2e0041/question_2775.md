# Q2775: bytes reuse stale verification state via run generator API arguments

## Question
Can an unprivileged attacker call the public Python API targeting `__bytes__` in `wheel/python/chia_rs/struct_stream.py` with run_generator API arguments when duplicate or prefix-colliding items are present make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:112` / `__bytes__`
- Entrypoint: call the public Python API
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `__bytes__` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
