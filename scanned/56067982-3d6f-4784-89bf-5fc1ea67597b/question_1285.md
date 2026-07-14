# Q1285: py calculate ip iters accept invalid consensus data via Python buffer objects and memoryview slices

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `py_calculate_ip_iters` in `wheel/src/api.rs` with Python buffer objects and memoryview slices when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:538` / `py_calculate_ip_iters`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: Python buffer objects and memoryview slices
- Exploit idea: Drive `py_calculate_ip_iters` through its public caller path using Python buffer objects and memoryview slices; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
