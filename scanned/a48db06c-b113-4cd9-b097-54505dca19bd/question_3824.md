# Q3824: update digest derive a different canonical hash via plot iteration boundary values

## Question
Can an unprivileged attacker validate plot/VDF/weight proof inputs targeting `update_digest` in `crates/chia-protocol/src/weight_proof.rs` with plot iteration boundary values when equivalent-looking encodings are mixed make chia_rs derive a different canonical hash, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/weight_proof.rs:23` / `update_digest`
- Entrypoint: validate plot/VDF/weight proof inputs
- Attacker controls: plot iteration boundary values
- Exploit idea: Drive `update_digest` through its public caller path using plot iteration boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
