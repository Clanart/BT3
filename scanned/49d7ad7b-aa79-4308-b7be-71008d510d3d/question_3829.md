# Q3829: is challenge collapse distinct inputs into one accepted state via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker submit proof and block challenge data targeting `is_challenge` in `crates/chia-protocol/src/weight_proof.rs` with weight proof summaries and sub-epoch data with default-enabled consensus flags make chia_rs collapse distinct inputs into one accepted state, violating the invariant that overflow block decisions are consistent at boundaries, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/weight_proof.rs:96` / `is_challenge`
- Entrypoint: submit proof and block challenge data
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `is_challenge` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: overflow block decisions are consistent at boundaries
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare quality string outputs across Rust and Python bindings.
