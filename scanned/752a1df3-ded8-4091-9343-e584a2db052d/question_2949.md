# Q2949: getstate commit output after an error path via macro-generated vector fields

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `__getstate__` in `crates/chia_py_streamable_macro/src/lib.rs` with macro-generated vector fields when equivalent-looking encodings are mixed make chia_rs commit output after an error path, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:413` / `__getstate__`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: macro-generated vector fields
- Exploit idea: Drive `__getstate__` through its public caller path using macro-generated vector fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
