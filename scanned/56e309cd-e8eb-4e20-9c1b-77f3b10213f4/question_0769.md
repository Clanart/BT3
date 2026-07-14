# Q769: update digest accept invalid consensus data via proof-of-space challenge/proof bytes

## Question
Can an unprivileged attacker submit proof and block challenge data targeting `update_digest` in `crates/chia-protocol/src/proof_of_space.rs` with proof-of-space challenge/proof bytes when equivalent-looking encodings are mixed make chia_rs accept invalid consensus data, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:227` / `update_digest`
- Entrypoint: submit proof and block challenge data
- Attacker controls: proof-of-space challenge/proof bytes
- Exploit idea: Drive `update_digest` through its public caller path using proof-of-space challenge/proof bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: test boundary iteration values against a simple arithmetic model.
