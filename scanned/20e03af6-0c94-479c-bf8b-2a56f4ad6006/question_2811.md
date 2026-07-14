# Q2811: Prover reuse stale verification state via run generator API arguments

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `Prover` in `wheel/src/api.rs` with run_generator API arguments when values sit exactly at max/min integer boundaries make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:656` / `Prover`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `Prover` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
