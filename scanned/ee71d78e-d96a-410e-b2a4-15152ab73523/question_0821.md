# Q821: FieldInfo produce a Rust/Python disagreement via allocator node pairs and atoms

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `FieldInfo` in `crates/clvm-derive/src/parser/field_info.rs` with allocator node pairs and atoms when serialized bytes are validly framed but semantically adversarial make chia_rs produce a Rust/Python disagreement, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-derive/src/parser/field_info.rs:5` / `FieldInfo`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `FieldInfo` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
