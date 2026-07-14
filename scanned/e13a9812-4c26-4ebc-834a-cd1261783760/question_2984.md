# Q2984: no overlapping bits allow replay across contexts via block record and sub-epoch edge values

## Question
Can an unprivileged attacker process valid-looking chain data at fork or height boundaries targeting `no_overlapping_bits` in `crates/chia-consensus/src/flags.rs` with block record and sub-epoch edge values when the same payload is parsed through public bindings make chia_rs allow replay across contexts, violating the invariant that block context remains deterministic, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/flags.rs:187` / `no_overlapping_bits`
- Entrypoint: process valid-looking chain data at fork or height boundaries
- Attacker controls: block record and sub-epoch edge values
- Exploit idea: Drive `no_overlapping_bits` through its public caller path using block record and sub-epoch edge values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: block context remains deterministic
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: property-test height/seconds constraints against modeled CoinRecord birth data.
