# Q1458: ConsensusFlags reuse stale verification state via consensus constants at activation boundaries

## Question
Can an unprivileged attacker process valid-looking chain data at fork or height boundaries targeting `ConsensusFlags` in `crates/chia-consensus/src/flags.rs` with consensus constants at activation boundaries when the same payload is parsed through public bindings make chia_rs reuse stale verification state, violating the invariant that time and height context cannot be bypassed, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/flags.rs:16` / `ConsensusFlags`
- Entrypoint: process valid-looking chain data at fork or height boundaries
- Attacker controls: consensus constants at activation boundaries
- Exploit idea: Drive `ConsensusFlags` through its public caller path using consensus constants at activation boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: time and height context cannot be bypassed
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: differential-test configured constants against expected block context calculations.
