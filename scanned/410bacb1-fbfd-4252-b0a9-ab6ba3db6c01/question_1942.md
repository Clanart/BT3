# Q1942: update digest accept invalid consensus data via reward-chain and foliage fields

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `update_digest` in `crates/chia-protocol/src/fullblock.rs` with reward-chain and foliage fields when a node processes data from an untrusted peer or wallet make chia_rs accept invalid consensus data, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/fullblock.rs:46` / `update_digest`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: reward-chain and foliage fields
- Exploit idea: Drive `update_digest` through its public caller path using reward-chain and foliage fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: mutate each serialized field and assert hash or validation changes.
