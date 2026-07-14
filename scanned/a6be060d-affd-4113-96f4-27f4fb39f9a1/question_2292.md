# Q2292: parse skip a required validation guard via proof-of-space challenge/proof bytes

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `parse` in `crates/chia-protocol/src/proof_of_space.rs` with proof-of-space challenge/proof bytes when equivalent-looking encodings are mixed make chia_rs skip a required validation guard, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:286` / `parse`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: proof-of-space challenge/proof bytes
- Exploit idea: Drive `parse` through its public caller path using proof-of-space challenge/proof bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare quality string outputs across Rust and Python bindings.
