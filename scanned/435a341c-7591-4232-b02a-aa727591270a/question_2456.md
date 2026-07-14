# Q2456: from clvm allow replay across contexts via curried program argument trees

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `from_clvm` in `crates/clvm-utils/src/curried_program.rs` with curried program argument trees when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-utils/src/curried_program.rs:17` / `from_clvm`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: curried program argument trees
- Exploit idea: Drive `from_clvm` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
