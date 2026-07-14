# Q2290: update digest accept invalid consensus data via overflow block signage point values

## Question
Can an unprivileged attacker validate plot/VDF/weight proof inputs targeting `update_digest` in `crates/chia-protocol/src/proof_of_space.rs` with overflow block signage point values when equivalent-looking encodings are mixed make chia_rs accept invalid consensus data, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:227` / `update_digest`
- Entrypoint: validate plot/VDF/weight proof inputs
- Attacker controls: overflow block signage point values
- Exploit idea: Drive `update_digest` through its public caller path using overflow block signage point values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: mutate VDF/classgroup bytes and assert verification/hash changes.
