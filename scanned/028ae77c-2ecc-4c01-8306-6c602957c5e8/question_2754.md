# Q2754: hexstr to bytes treat malformed data as a valid empty/default value via Python buffer objects and memoryview slices

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `hexstr_to_bytes` in `wheel/python/chia_rs/sized_byte_class.py` with Python buffer objects and memoryview slices at a fork-height or boundary-value activation point make chia_rs treat malformed data as a valid empty/default value, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/sized_byte_class.py:20` / `hexstr_to_bytes`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: Python buffer objects and memoryview slices
- Exploit idea: Drive `hexstr_to_bytes` through its public caller path using Python buffer objects and memoryview slices; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
