# Q990: new reuse stale verification state via memo and proof structures

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `new` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with memo and proof structures when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that lineage proofs cannot be reused for unrelated coins, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:71` / `new`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: memo and proof structures
- Exploit idea: Drive `new` through its public caller path using memo and proof structures; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: lineage proofs cannot be reused for unrelated coins
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
