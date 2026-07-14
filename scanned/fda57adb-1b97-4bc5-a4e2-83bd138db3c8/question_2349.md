# Q2349: VariantInfo commit output after an error path via FromClvm/ToClvm enum discriminants

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `VariantInfo` in `crates/clvm-derive/src/parser/variant_info.rs` with FromClvm/ToClvm enum discriminants when serialized bytes are validly framed but semantically adversarial make chia_rs commit output after an error path, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-derive/src/parser/variant_info.rs:5` / `VariantInfo`
- Entrypoint: hash curried CLVM programs
- Attacker controls: FromClvm/ToClvm enum discriminants
- Exploit idea: Drive `VariantInfo` through its public caller path using FromClvm/ToClvm enum discriminants; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test curried tree hash against executing the curried program.
