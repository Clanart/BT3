# Q3980: curry derive a different canonical hash via FromClvm/ToClvm enum discriminants

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `curry` in `crates/clvm-utils/src/curried_program.rs` with FromClvm/ToClvm enum discriminants when duplicate or prefix-colliding items are present make chia_rs derive a different canonical hash, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-utils/src/curried_program.rs:65` / `curry`
- Entrypoint: hash curried CLVM programs
- Attacker controls: FromClvm/ToClvm enum discriminants
- Exploit idea: Drive `curry` through its public caller path using FromClvm/ToClvm enum discriminants; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
