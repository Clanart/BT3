# Q2769: init commit output after an error path via run generator API arguments

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `__init__` in `wheel/python/chia_rs/struct_stream.py` with run_generator API arguments when the same payload is parsed through public bindings make chia_rs commit output after an error path, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:73` / `__init__`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `__init__` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
