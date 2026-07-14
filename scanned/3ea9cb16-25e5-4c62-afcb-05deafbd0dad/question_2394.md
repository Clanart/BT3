# Q2394: from clvm treat malformed data as a valid empty/default value via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `from_clvm` in `crates/clvm-traits/src/from_clvm.rs` with CLVM atoms with redundant sign bytes when the payload is accepted by one public API before another validates it make chia_rs treat malformed data as a valid empty/default value, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-traits/src/from_clvm.rs:180` / `from_clvm`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `from_clvm` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
