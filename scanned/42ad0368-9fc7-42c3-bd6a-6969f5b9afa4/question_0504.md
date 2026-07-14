# Q504: update digest commit output after an error path via unfinished block payloads

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `update_digest` in `crates/chia-protocol/src/program.rs` with unfinished block payloads when the attacker can choose ordering inside a batch make chia_rs commit output after an error path, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/program.rs:431` / `update_digest`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `update_digest` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: parse-stream-hash round-trip the object and compare field hashes.
