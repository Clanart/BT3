# Q1247: parse metadata from name allow replay across contexts via PyO3 object extraction values

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `parse_metadata_from_name` in `wheel/python/chia_rs/struct_stream.py` with PyO3 object extraction values when duplicate or prefix-colliding items are present make chia_rs allow replay across contexts, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:23` / `parse_metadata_from_name`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `parse_metadata_from_name` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
