# Q1246: trunc mis-order operations across a batch via run generator API arguments

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `__trunc__` in `wheel/python/chia_rs/struct_stream.py` with run_generator API arguments when duplicate or prefix-colliding items are present make chia_rs mis-order operations across a batch, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:20` / `__trunc__`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `__trunc__` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
