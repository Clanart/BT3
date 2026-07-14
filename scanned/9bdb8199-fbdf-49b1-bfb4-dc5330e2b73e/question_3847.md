# Q3847: DiscriminantInfo accept invalid consensus data via curried program argument trees

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `DiscriminantInfo` in `crates/clvm-derive/src/helpers.rs` with curried program argument trees at a fork-height or boundary-value activation point make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-derive/src/helpers.rs:15` / `DiscriminantInfo`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: curried program argument trees
- Exploit idea: Drive `DiscriminantInfo` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
