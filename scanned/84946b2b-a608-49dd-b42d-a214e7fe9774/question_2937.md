# Q2937: from json dict commit output after an error path via macro-generated vector fields

## Question
Can an unprivileged attacker compute streamable hashes targeting `from_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with macro-generated vector fields when a node processes data from an untrusted peer or wallet make chia_rs commit output after an error path, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:245` / `from_json_dict`
- Entrypoint: compute streamable hashes
- Attacker controls: macro-generated vector fields
- Exploit idea: Drive `from_json_dict` through its public caller path using macro-generated vector fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
