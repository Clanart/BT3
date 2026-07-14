# Q1001: did solution produce a Rust/Python disagreement via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `did_solution` in `crates/chia-puzzle-types/src/puzzles/did.rs` with royalty and settlement puzzle fields with default-enabled consensus flags make chia_rs produce a Rust/Python disagreement, violating the invariant that lineage proofs cannot be reused for unrelated coins, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/did.rs:93` / `did_solution`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `did_solution` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: lineage proofs cannot be reused for unrelated coins
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
