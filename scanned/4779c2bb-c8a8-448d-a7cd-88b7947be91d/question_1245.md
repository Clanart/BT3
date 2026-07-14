# Q1245: spend module treat malformed data as a valid empty/default value via from bytes/from json dict inputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `spend_module` in `wheel/python/chia_rs/spend.py` with from_bytes/from_json_dict inputs when duplicate or prefix-colliding items are present make chia_rs treat malformed data as a valid empty/default value, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/python/chia_rs/spend.py:1` / `spend_module`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `spend_module` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
