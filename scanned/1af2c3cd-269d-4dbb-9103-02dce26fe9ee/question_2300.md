# Q2300: VDFInfo allow replay across contexts via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `VDFInfo` in `crates/chia-protocol/src/vdf.rs` with weight proof summaries and sub-epoch data with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/vdf.rs:7` / `VDFInfo`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `VDFInfo` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
