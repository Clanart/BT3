# Q3530: from overflow or underflow a boundary check via Program bytes passed through streamable parsing

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `from` in `crates/chia-protocol/src/program.rs` with Program bytes passed through streamable parsing when the same payload is parsed through public bindings make chia_rs overflow or underflow a boundary check, violating the invariant that trusted and untrusted parsing cannot disagree on valid network bytes, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/program.rs:102` / `from`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: Program bytes passed through streamable parsing
- Exploit idea: Drive `from` through its public caller path using Program bytes passed through streamable parsing; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted and untrusted parsing cannot disagree on valid network bytes
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
