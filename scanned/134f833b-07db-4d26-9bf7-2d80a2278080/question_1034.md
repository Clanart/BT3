# Q1034: new derive a different canonical hash via lineage proofs and launcher ids

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `new` in `crates/chia-puzzle-types/src/puzzles/standard.rs` with lineage proofs and launcher ids when duplicate or prefix-colliding items are present make chia_rs derive a different canonical hash, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/standard.rs:15` / `new`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: lineage proofs and launcher ids
- Exploit idea: Drive `new` through its public caller path using lineage proofs and launcher ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
