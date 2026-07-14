# Q2771: parse derive a different canonical hash via cross-language conversion outputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `parse` in `wheel/python/chia_rs/struct_stream.py` with cross-language conversion outputs when the same payload is parsed through public bindings make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:94` / `parse`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: cross-language conversion outputs
- Exploit idea: Drive `parse` through its public caller path using cross-language conversion outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
