# Q3871: VariantKind accept invalid consensus data via curried program argument trees

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `VariantKind` in `crates/clvm-derive/src/parser/variant_info.rs` with curried program argument trees when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-derive/src/parser/variant_info.rs:14` / `VariantKind`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: curried program argument trees
- Exploit idea: Drive `VariantKind` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
