# Q799: impl for struct collapse distinct inputs into one accepted state via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `impl_for_struct` in `crates/clvm-derive/src/from_clvm.rs` with CLVM atoms with redundant sign bytes when the same payload is parsed through public bindings make chia_rs collapse distinct inputs into one accepted state, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-derive/src/from_clvm.rs:193` / `impl_for_struct`
- Entrypoint: hash curried CLVM programs
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `impl_for_struct` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
