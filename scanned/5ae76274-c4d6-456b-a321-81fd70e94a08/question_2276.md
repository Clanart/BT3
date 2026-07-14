# Q2276: calculate sp iters allow replay across contexts via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `calculate_sp_iters` in `crates/chia-protocol/src/pot_iterations.rs` with weight proof summaries and sub-epoch data when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/pot_iterations.rs:40` / `calculate_sp_iters`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `calculate_sp_iters` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
