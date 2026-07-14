# Q1282: py is overflow block mis-order operations across a batch via run generator API arguments

## Question
Can an unprivileged attacker call the public Python API targeting `py_is_overflow_block` in `wheel/src/api.rs` with run_generator API arguments when values sit exactly at max/min integer boundaries make chia_rs mis-order operations across a batch, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:499` / `py_is_overflow_block`
- Entrypoint: call the public Python API
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `py_is_overflow_block` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
