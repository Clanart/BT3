# Q2823: validate proof v2 reuse stale verification state via run generator API arguments

## Question
Can an unprivileged attacker call the public Python API targeting `validate_proof_v2` in `wheel/src/api.rs` with run_generator API arguments when a node processes data from an untrusted peer or wallet make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:717` / `validate_proof_v2`
- Entrypoint: call the public Python API
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `validate_proof_v2` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
