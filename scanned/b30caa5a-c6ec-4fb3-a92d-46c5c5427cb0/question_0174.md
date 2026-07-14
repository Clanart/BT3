# Q174: from leafs reuse stale verification state via proofs for absent and present leaves sharing prefixes

## Question
Can an unprivileged attacker compute a Merkle root from attacker-controlled leaves targeting `from_leafs` in `crates/chia-consensus/src/merkle_tree.rs` with proofs for absent and present leaves sharing prefixes when the attacker can choose ordering inside a batch make chia_rs reuse stale verification state, violating the invariant that Merkle roots uniquely bind included and excluded leaves, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:399` / `from_leafs`
- Entrypoint: compute a Merkle root from attacker-controlled leaves
- Attacker controls: proofs for absent and present leaves sharing prefixes
- Exploit idea: Drive `from_leafs` through its public caller path using proofs for absent and present leaves sharing prefixes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Merkle roots uniquely bind included and excluded leaves
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: validate inclusion and exclusion proofs for neighboring leaves.
