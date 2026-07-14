# Q2536: new collapse distinct inputs into one accepted state via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `new` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with royalty and settlement puzzle fields at a fork-height or boundary-value activation point make chia_rs collapse distinct inputs into one accepted state, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:145` / `new`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `new` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
