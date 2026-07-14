# Q1278: fast forward singleton reuse stale verification state via cross-language conversion outputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `fast_forward_singleton` in `wheel/src/api.rs` with cross-language conversion outputs when values sit exactly at max/min integer boundaries make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:436` / `fast_forward_singleton`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: cross-language conversion outputs
- Exploit idea: Drive `fast_forward_singleton` through its public caller path using cross-language conversion outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
