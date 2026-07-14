# Q1251: stream skip a required validation guard via from bytes/from json dict inputs

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `stream` in `wheel/python/chia_rs/struct_stream.py` with from_bytes/from_json_dict inputs when duplicate or prefix-colliding items are present make chia_rs skip a required validation guard, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:98` / `stream`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `stream` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
