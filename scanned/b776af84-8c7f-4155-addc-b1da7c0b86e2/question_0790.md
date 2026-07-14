# Q790: RecentChainData mis-order operations across a batch via plot iteration boundary values

## Question
Can an unprivileged attacker derive quality strings from proof bytes targeting `RecentChainData` in `crates/chia-protocol/src/weight_proof.rs` with plot iteration boundary values at a fork-height or boundary-value activation point make chia_rs mis-order operations across a batch, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/weight_proof.rs:115` / `RecentChainData`
- Entrypoint: derive quality strings from proof bytes
- Attacker controls: plot iteration boundary values
- Exploit idea: Drive `RecentChainData` through its public caller path using plot iteration boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: test boundary iteration values against a simple arithmetic model.
