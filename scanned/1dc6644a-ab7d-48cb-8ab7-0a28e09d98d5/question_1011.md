# Q1011: new skip a required validation guard via synthetic key derivation inputs

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `new` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with synthetic key derivation inputs at a fork-height or boundary-value activation point make chia_rs skip a required validation guard, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:97` / `new`
- Entrypoint: parse puzzle solution structures
- Attacker controls: synthetic key derivation inputs
- Exploit idea: Drive `new` through its public caller path using synthetic key derivation inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
