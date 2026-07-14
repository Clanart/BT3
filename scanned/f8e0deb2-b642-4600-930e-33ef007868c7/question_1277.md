# Q1277: supports fast forward produce a Rust/Python disagreement via PyO3 object extraction values

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `supports_fast_forward` in `wheel/src/api.rs` with PyO3 object extraction values when values sit exactly at max/min integer boundaries make chia_rs produce a Rust/Python disagreement, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:403` / `supports_fast_forward`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `supports_fast_forward` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
