# Q3036: new skip a required validation guard via public API arguments

## Question
Can an unprivileged attacker batch repeated API calls targeting `new` in `crates/chia-sha2/src/lib.rs` with public API arguments when values sit exactly at max/min integer boundaries make chia_rs skip a required validation guard, violating the invariant that public API outputs remain deterministic for identical inputs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-sha2/src/lib.rs:14` / `new`
- Entrypoint: batch repeated API calls
- Attacker controls: public API arguments
- Exploit idea: Drive `new` through its public caller path using public API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: public API outputs remain deterministic for identical inputs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz public API inputs and compare with a small reference model.
