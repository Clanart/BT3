# Q1261: SignedSafeMath.toUint256 - targetVote minus currentVote is computed on two branches

## Question
Note that in libraries/SignedSafeMath.sol, castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Can an attacker holding only tokens bought on market reach it via `toUint256(int256 value)` under the delegated pool's votes are included in the pool total but not in the denominator and force `int256 delta supplied by the voter` apart from `uint256 pool.totalVoteInVlmgp`, breaking the invariant that one arithmetic expression must produce the value pushed to an external gauge for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: the delegated pool's votes are included in the pool total but not in the denominator.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the delegated pool's votes are included in the pool total but not in the denominator, have the attacker run `toUint256(int256 value)`, then assert the victim's claimable value and the `int256 delta supplied by the voter` versus `uint256 pool.totalVoteInVlmgp` relation are unchanged by the attacker's transaction.
