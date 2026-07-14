# Q998: curry tree hash derive a different canonical hash via lineage proofs and launcher ids

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/did.rs` with lineage proofs and launcher ids with default-enabled consensus flags make chia_rs derive a different canonical hash, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/did.rs:39` / `curry_tree_hash`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: lineage proofs and launcher ids
- Exploit idea: Drive `curry_tree_hash` through its public caller path using lineage proofs and launcher ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz synthetic key inputs and assert signature authorization is unchanged.
