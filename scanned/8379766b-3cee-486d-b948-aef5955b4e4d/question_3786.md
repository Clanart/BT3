# Q3786: get string commit output after an error path via VDF/classgroup byte encodings

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `get_string` in `crates/chia-protocol/src/partial_proof.rs` with VDF/classgroup byte encodings when values sit exactly at max/min integer boundaries make chia_rs commit output after an error path, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:15` / `get_string`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: VDF/classgroup byte encodings
- Exploit idea: Drive `get_string` through its public caller path using VDF/classgroup byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare quality string outputs across Rust and Python bindings.
