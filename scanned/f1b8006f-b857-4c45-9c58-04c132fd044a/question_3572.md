# Q3572: update digest derive a different canonical hash via Program bytes passed through streamable parsing

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `update_digest` in `crates/chia-protocol/src/unfinished_block.rs` with Program bytes passed through streamable parsing when values sit exactly at max/min integer boundaries make chia_rs derive a different canonical hash, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/unfinished_block.rs:43` / `update_digest`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: Program bytes passed through streamable parsing
- Exploit idea: Drive `update_digest` through its public caller path using Program bytes passed through streamable parsing; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: parse-stream-hash round-trip the object and compare field hashes.
