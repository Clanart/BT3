# Q2051: update digest derive a different canonical hash via unfinished block payloads

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `update_digest` in `crates/chia-protocol/src/unfinished_block.rs` with unfinished block payloads when a node processes data from an untrusted peer or wallet make chia_rs derive a different canonical hash, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/unfinished_block.rs:43` / `update_digest`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `update_digest` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
