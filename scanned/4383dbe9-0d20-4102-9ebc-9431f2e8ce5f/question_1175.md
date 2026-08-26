# Q1175: SignedSafeMath.wmul - targetVote minus currentVote is computed on two branches

## Question
libraries/SignedSafeMath.sol: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Under the delegated pool's votes are included in the pool total but not in the denominator, is there an unprivileged sequence of `wmul(int256 x, int256 y)` that leaves `totalUserVote accumulated as int256` unreconciled with `userTotalVotedInVlmgp as uint256`, violates the invariant that one arithmetic expression must produce the value pushed to an external gauge, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: the delegated pool's votes are included in the pool total but not in the denominator.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the delegated pool's votes are included in the pool total but not in the denominator, then assert `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` end identical in both runs.
