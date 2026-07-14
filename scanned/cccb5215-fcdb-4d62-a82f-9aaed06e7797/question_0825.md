# Q825: StructInfo treat malformed data as a valid empty/default value via curried program argument trees

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `StructInfo` in `crates/clvm-derive/src/parser/struct_info.rs` with curried program argument trees when serialized bytes are validly framed but semantically adversarial make chia_rs treat malformed data as a valid empty/default value, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-derive/src/parser/struct_info.rs:5` / `StructInfo`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: curried program argument trees
- Exploit idea: Drive `StructInfo` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
