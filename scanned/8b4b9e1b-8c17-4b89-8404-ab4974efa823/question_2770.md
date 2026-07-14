# Q2770: construct optional accept invalid consensus data via PyO3 object extraction values

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `construct_optional` in `wheel/python/chia_rs/struct_stream.py` with PyO3 object extraction values when the same payload is parsed through public bindings make chia_rs accept invalid consensus data, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:85` / `construct_optional`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `construct_optional` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
