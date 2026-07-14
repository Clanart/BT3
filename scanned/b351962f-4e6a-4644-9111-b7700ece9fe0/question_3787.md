# Q3787: serialize quality accept invalid consensus data via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `serialize_quality` in `crates/chia-protocol/src/partial_proof.rs` with weight proof summaries and sub-epoch data when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:31` / `serialize_quality`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `serialize_quality` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare quality string outputs across Rust and Python bindings.
