# Q1290: Prover reuse stale verification state via cross-language conversion outputs

## Question
Can an unprivileged attacker call the public Python API targeting `Prover` in `wheel/src/api.rs` with cross-language conversion outputs when a node processes data from an untrusted peer or wallet make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:656` / `Prover`
- Entrypoint: call the public Python API
- Attacker controls: cross-language conversion outputs
- Exploit idea: Drive `Prover` through its public caller path using cross-language conversion outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
