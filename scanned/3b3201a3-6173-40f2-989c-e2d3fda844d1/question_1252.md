# Q1252: from bytes mis-bind attacker-controlled bytes to trusted state via run generator API arguments

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `from_bytes` in `wheel/python/chia_rs/struct_stream.py` with run_generator API arguments when duplicate or prefix-colliding items are present make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:102` / `from_bytes`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `from_bytes` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
