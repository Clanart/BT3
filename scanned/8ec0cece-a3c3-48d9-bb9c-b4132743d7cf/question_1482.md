# Q1482: from reuse stale verification state via consensus constants at activation boundaries

## Question
Can an unprivileged attacker process valid-looking chain data at fork or height boundaries targeting `from` in `crates/chia-consensus/src/validation_error.rs` with consensus constants at activation boundaries when serialized bytes are validly framed but semantically adversarial make chia_rs reuse stale verification state, violating the invariant that time and height context cannot be bypassed, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/validation_error.rs:204` / `from`
- Entrypoint: process valid-looking chain data at fork or height boundaries
- Attacker controls: consensus constants at activation boundaries
- Exploit idea: Drive `from` through its public caller path using consensus constants at activation boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: time and height context cannot be bypassed
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: run boundary-height fixtures under adjacent flags and assert expected fork behavior.
