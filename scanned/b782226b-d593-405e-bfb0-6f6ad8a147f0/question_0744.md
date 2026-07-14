# Q744: get string commit output after an error path via partial proof quality strings

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `get_string` in `crates/chia-protocol/src/partial_proof.rs` with partial proof quality strings when a node processes data from an untrusted peer or wallet make chia_rs commit output after an error path, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:15` / `get_string`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: partial proof quality strings
- Exploit idea: Drive `get_string` through its public caller path using partial proof quality strings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
