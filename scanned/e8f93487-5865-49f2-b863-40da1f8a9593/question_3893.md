# Q3893: encode curried arg allow replay across contexts via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `encode_curried_arg` in `crates/clvm-traits/src/clvm_encoder.rs` with CLVM atoms with redundant sign bytes when the attacker can choose ordering inside a batch make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-traits/src/clvm_encoder.rs:31` / `encode_curried_arg`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `encode_curried_arg` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
