# Q2774: stream to bytes produce a Rust/Python disagreement via from bytes/from json dict inputs

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `stream_to_bytes` in `wheel/python/chia_rs/struct_stream.py` with from_bytes/from_json_dict inputs when duplicate or prefix-colliding items are present make chia_rs produce a Rust/Python disagreement, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:109` / `stream_to_bytes`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `stream_to_bytes` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
