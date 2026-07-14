# Q438: py get included reward coins reuse stale verification state via unfinished block payloads

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `py_get_included_reward_coins` in `crates/chia-protocol/src/fullblock.rs` with unfinished block payloads when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that coin and block hashes bind every consensus-critical field, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/fullblock.rs:286` / `py_get_included_reward_coins`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `py_get_included_reward_coins` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: coin and block hashes bind every consensus-critical field
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
