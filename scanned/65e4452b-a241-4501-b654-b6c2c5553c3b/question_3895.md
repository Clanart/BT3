# Q3895: encode atom accept invalid consensus data via curried program argument trees

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `encode_atom` in `crates/clvm-traits/src/clvm_encoder.rs` with curried program argument trees when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-traits/src/clvm_encoder.rs:51` / `encode_atom`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: curried program argument trees
- Exploit idea: Drive `encode_atom` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
