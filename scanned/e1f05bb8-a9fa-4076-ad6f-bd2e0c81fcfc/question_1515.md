# Q1515: new skip a required validation guard via cross-crate conversion values

## Question
Can an unprivileged attacker pass untrusted serialized values targeting `new` in `crates/chia-sha2/src/lib.rs` with cross-crate conversion values when a node processes data from an untrusted peer or wallet make chia_rs skip a required validation guard, violating the invariant that public API outputs remain deterministic for identical inputs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-sha2/src/lib.rs:14` / `new`
- Entrypoint: pass untrusted serialized values
- Attacker controls: cross-crate conversion values
- Exploit idea: Drive `new` through its public caller path using cross-crate conversion values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: public API outputs remain deterministic for identical inputs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: test numeric boundary inputs for overflow and canonical error paths.
