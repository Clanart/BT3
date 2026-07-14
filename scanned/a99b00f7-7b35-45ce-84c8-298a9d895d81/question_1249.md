# Q1249: construct optional accept invalid consensus data via Python buffer objects and memoryview slices

## Question
Can an unprivileged attacker call the public Python API targeting `construct_optional` in `wheel/python/chia_rs/struct_stream.py` with Python buffer objects and memoryview slices when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:85` / `construct_optional`
- Entrypoint: call the public Python API
- Attacker controls: Python buffer objects and memoryview slices
- Exploit idea: Drive `construct_optional` through its public caller path using Python buffer objects and memoryview slices; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
