# Q2816: get strength allow replay across contexts via from bytes/from json dict inputs

## Question
Can an unprivileged attacker call the public Python API targeting `get_strength` in `wheel/src/api.rs` with from_bytes/from_json_dict inputs when values sit exactly at max/min integer boundaries make chia_rs allow replay across contexts, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:683` / `get_strength`
- Entrypoint: call the public Python API
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `get_strength` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
