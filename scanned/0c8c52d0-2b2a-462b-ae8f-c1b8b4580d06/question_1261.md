# Q1261: compute plot group id v2 accept invalid consensus data via Python buffer objects and memoryview slices

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `compute_plot_group_id_v2` in `wheel/src/api.rs` with Python buffer objects and memoryview slices when serialized bytes are validly framed but semantically adversarial make chia_rs accept invalid consensus data, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:163` / `compute_plot_group_id_v2`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: Python buffer objects and memoryview slices
- Exploit idea: Drive `compute_plot_group_id_v2` through its public caller path using Python buffer objects and memoryview slices; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
