# Q2810: create v2 plot produce a Rust/Python disagreement via from bytes/from json dict inputs

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `create_v2_plot` in `wheel/src/api.rs` with from_bytes/from_json_dict inputs when values sit exactly at max/min integer boundaries make chia_rs produce a Rust/Python disagreement, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:633` / `create_v2_plot`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `create_v2_plot` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
