# Q1250: parse derive a different canonical hash via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker call the public Python API targeting `parse` in `wheel/python/chia_rs/struct_stream.py` with Python lists of tuple spend inputs when duplicate or prefix-colliding items are present make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:94` / `parse`
- Entrypoint: call the public Python API
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `parse` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
