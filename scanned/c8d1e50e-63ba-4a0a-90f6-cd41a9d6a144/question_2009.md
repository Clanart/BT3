# Q2009: from overflow or underflow a boundary check via unfinished block payloads

## Question
Can an unprivileged attacker submit serialized block or spend data targeting `from` in `crates/chia-protocol/src/program.rs` with unfinished block payloads when duplicate or prefix-colliding items are present make chia_rs overflow or underflow a boundary check, violating the invariant that trusted and untrusted parsing cannot disagree on valid network bytes, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/program.rs:102` / `from`
- Entrypoint: submit serialized block or spend data
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `from` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted and untrusted parsing cannot disagree on valid network bytes
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Rust and Python object construction from the same bytes.
