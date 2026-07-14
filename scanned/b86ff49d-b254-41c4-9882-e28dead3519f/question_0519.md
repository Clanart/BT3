# Q519: get unfinished skip a required validation guard via CoinState/CoinRecord transition sequences

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `get_unfinished` in `crates/chia-protocol/src/reward_chain_block.rs` with CoinState/CoinRecord transition sequences when a node processes data from an untrusted peer or wallet make chia_rs skip a required validation guard, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/reward_chain_block.rs:130` / `get_unfinished`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: CoinState/CoinRecord transition sequences
- Exploit idea: Drive `get_unfinished` through its public caller path using CoinState/CoinRecord transition sequences; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
