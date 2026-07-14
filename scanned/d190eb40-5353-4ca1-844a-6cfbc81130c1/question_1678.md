# Q1678: ArrayTypes accept invalid consensus data via large but valid spend bundle outputs

## Question
Can an unprivileged attacker derive additions/removals for a candidate block targeting `ArrayTypes` in `crates/chia-consensus/src/merkle_tree.rs` with large but valid spend bundle outputs when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that additions and removals exactly match accepted spends, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:25` / `ArrayTypes`
- Entrypoint: derive additions/removals for a candidate block
- Attacker controls: large but valid spend bundle outputs
- Exploit idea: Drive `ArrayTypes` through its public caller path using large but valid spend bundle outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: additions and removals exactly match accepted spends
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: generate duplicate-prefix leaves and verify roots/proofs against an independent model.
