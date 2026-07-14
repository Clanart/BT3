# Q1033: StandardArgs accept invalid consensus data via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `StandardArgs` in `crates/chia-puzzle-types/src/puzzles/standard.rs` with CAT/NFT/DID/offer/singleton puzzle arguments when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/standard.rs:10` / `StandardArgs`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `StandardArgs` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
