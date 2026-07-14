# Q743: PartialProof allow replay across contexts via overflow block signage point values

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `PartialProof` in `crates/chia-protocol/src/partial_proof.rs` with overflow block signage point values when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:9` / `PartialProof`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: overflow block signage point values
- Exploit idea: Drive `PartialProof` through its public caller path using overflow block signage point values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
