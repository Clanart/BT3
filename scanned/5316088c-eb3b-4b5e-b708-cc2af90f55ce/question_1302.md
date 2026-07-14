# Q1302: validate proof v2 reuse stale verification state via cross-language conversion outputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `validate_proof_v2` in `wheel/src/api.rs` with cross-language conversion outputs when the payload is accepted by one public API before another validates it make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:717` / `validate_proof_v2`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: cross-language conversion outputs
- Exploit idea: Drive `validate_proof_v2` through its public caller path using cross-language conversion outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
