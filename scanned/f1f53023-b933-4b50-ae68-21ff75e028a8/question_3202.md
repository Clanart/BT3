# Q3202: from proof mis-bind attacker-controlled bytes to trusted state via proofs for absent and present leaves sharing prefixes

## Question
Can an unprivileged attacker compute a Merkle root from attacker-controlled leaves targeting `from_proof` in `crates/chia-consensus/src/merkle_tree.rs` with proofs for absent and present leaves sharing prefixes when duplicate or prefix-colliding items are present make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that Merkle roots uniquely bind included and excluded leaves, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:62` / `from_proof`
- Entrypoint: compute a Merkle root from attacker-controlled leaves
- Attacker controls: proofs for absent and present leaves sharing prefixes
- Exploit idea: Drive `from_proof` through its public caller path using proofs for absent and present leaves sharing prefixes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Merkle roots uniquely bind included and excluded leaves
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: validate inclusion and exclusion proofs for neighboring leaves.
