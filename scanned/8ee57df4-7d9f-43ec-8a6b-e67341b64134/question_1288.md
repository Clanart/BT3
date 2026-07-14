# Q1288: py is canonical serialization mis-bind attacker-controlled bytes to trusted state via run generator API arguments

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `py_is_canonical_serialization` in `wheel/src/api.rs` with run_generator API arguments when a node processes data from an untrusted peer or wallet make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:627` / `py_is_canonical_serialization`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `py_is_canonical_serialization` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
