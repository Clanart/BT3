# Q2294: pool pk produce a Rust/Python disagreement via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker calculate plot iterations at boundary values targeting `pool_pk` in `crates/chia-protocol/src/proof_of_space.rs` with weight proof summaries and sub-epoch data when equivalent-looking encodings are mixed make chia_rs produce a Rust/Python disagreement, violating the invariant that invalid proofs cannot produce valid quality strings, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:358` / `pool_pk`
- Entrypoint: calculate plot iterations at boundary values
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `pool_pk` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid proofs cannot produce valid quality strings
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare quality string outputs across Rust and Python bindings.
