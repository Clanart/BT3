# Q3979: check accept invalid consensus data via curried program argument trees

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `check` in `crates/clvm-utils/src/curried_program.rs` with curried program argument trees when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-utils/src/curried_program.rs:43` / `check`
- Entrypoint: hash curried CLVM programs
- Attacker controls: curried program argument trees
- Exploit idea: Drive `check` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
