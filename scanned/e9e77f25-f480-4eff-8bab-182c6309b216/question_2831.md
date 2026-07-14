# Q2831: run block generator derive a different canonical hash via cross-language conversion outputs

## Question
Can an unprivileged attacker call the public Python API targeting `run_block_generator` in `wheel/src/run_generator.rs` with cross-language conversion outputs when the payload is accepted by one public API before another validates it make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/run_generator.rs:28` / `run_block_generator`
- Entrypoint: call the public Python API
- Attacker controls: cross-language conversion outputs
- Exploit idea: Drive `run_block_generator` through its public caller path using cross-language conversion outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
