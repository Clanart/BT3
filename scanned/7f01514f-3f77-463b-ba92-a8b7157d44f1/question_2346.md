# Q2346: StructInfo treat malformed data as a valid empty/default value via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `StructInfo` in `crates/clvm-derive/src/parser/struct_info.rs` with CLVM atoms with redundant sign bytes when serialized bytes are validly framed but semantically adversarial make chia_rs treat malformed data as a valid empty/default value, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-derive/src/parser/struct_info.rs:5` / `StructInfo`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `StructInfo` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test curried tree hash against executing the curried program.
