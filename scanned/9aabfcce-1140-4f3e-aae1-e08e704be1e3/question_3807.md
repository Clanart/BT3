# Q3807: quality string treat malformed data as a valid empty/default value via overflow block signage point values

## Question
Can an unprivileged attacker validate plot/VDF/weight proof inputs targeting `quality_string` in `crates/chia-protocol/src/proof_of_space.rs` with overflow block signage point values when the payload is accepted by one public API before another validates it make chia_rs treat malformed data as a valid empty/default value, violating the invariant that overflow block decisions are consistent at boundaries, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:162` / `quality_string`
- Entrypoint: validate plot/VDF/weight proof inputs
- Attacker controls: overflow block signage point values
- Exploit idea: Drive `quality_string` through its public caller path using overflow block signage point values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: overflow block decisions are consistent at boundaries
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare quality string outputs across Rust and Python bindings.
