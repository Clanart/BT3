# Q765: quality string treat malformed data as a valid empty/default value via weight proof summaries and sub-epoch data

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `quality_string` in `crates/chia-protocol/src/proof_of_space.rs` with weight proof summaries and sub-epoch data when equivalent-looking encodings are mixed make chia_rs treat malformed data as a valid empty/default value, violating the invariant that overflow block decisions are consistent at boundaries, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/proof_of_space.rs:162` / `quality_string`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: weight proof summaries and sub-epoch data
- Exploit idea: Drive `quality_string` through its public caller path using weight proof summaries and sub-epoch data; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: overflow block decisions are consistent at boundaries
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz proof bytes and assert invalid proofs never produce accepted quality.
