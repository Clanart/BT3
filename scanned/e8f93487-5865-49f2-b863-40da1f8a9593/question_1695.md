# Q1695: from leafs reuse stale verification state via hint-bearing CREATE COIN outputs

## Question
Can an unprivileged attacker request additions/removals from a generator targeting `from_leafs` in `crates/chia-consensus/src/merkle_tree.rs` with hint-bearing CREATE_COIN outputs when the attacker can choose ordering inside a batch make chia_rs reuse stale verification state, violating the invariant that Merkle roots uniquely bind included and excluded leaves, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:399` / `from_leafs`
- Entrypoint: request additions/removals from a generator
- Attacker controls: hint-bearing CREATE_COIN outputs
- Exploit idea: Drive `from_leafs` through its public caller path using hint-bearing CREATE_COIN outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Merkle roots uniquely bind included and excluded leaves
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare computed roots before and after sorted/duplicated leaf normalization.
