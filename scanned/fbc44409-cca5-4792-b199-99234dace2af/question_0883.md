# Q883: decode number collapse distinct inputs into one accepted state via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `decode_number` in `crates/clvm-traits/src/int_encoding.rs` with CLVM atoms with redundant sign bytes with default-enabled consensus flags make chia_rs collapse distinct inputs into one accepted state, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-traits/src/int_encoding.rs:35` / `decode_number`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `decode_number` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
