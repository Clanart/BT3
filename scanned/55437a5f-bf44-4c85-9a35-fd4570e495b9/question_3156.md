# Q3156: curry single arg reuse stale verification state via referenced generator list ordering

## Question
Can an unprivileged attacker fast-forward a singleton spend with attacker-controlled lineage targeting `curry_single_arg` in `crates/chia-consensus/src/fast_forward.rs` with referenced generator list ordering when the payload is accepted by one public API before another validates it make chia_rs reuse stale verification state, violating the invariant that generator references cannot change spend meaning, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/fast_forward.rs:18` / `curry_single_arg`
- Entrypoint: fast-forward a singleton spend with attacker-controlled lineage
- Attacker controls: referenced generator list ordering
- Exploit idea: Drive `curry_single_arg` through its public caller path using referenced generator list ordering; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generator references cannot change spend meaning
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz generator refs/backrefs and assert deterministic output.
