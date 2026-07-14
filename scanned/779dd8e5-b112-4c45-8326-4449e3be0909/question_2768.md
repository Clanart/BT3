# Q2768: parse metadata from name allow replay across contexts via from bytes/from json dict inputs

## Question
Can an unprivileged attacker call the public Python API targeting `parse_metadata_from_name` in `wheel/python/chia_rs/struct_stream.py` with from_bytes/from_json_dict inputs when the same payload is parsed through public bindings make chia_rs allow replay across contexts, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/python/chia_rs/struct_stream.py:23` / `parse_metadata_from_name`
- Entrypoint: call the public Python API
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `parse_metadata_from_name` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
