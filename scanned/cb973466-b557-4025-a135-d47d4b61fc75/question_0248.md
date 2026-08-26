# Q0248: SignedSafeMath.wdiv - targetVote minus currentVote is computed on two branches

## Question
Consider libraries/SignedSafeMath.sol, where castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Assuming the voter supplies offsetting positive and negative deltas that net to zero, can an unprivileged attacker turn this into a divergence between `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` via `wdiv(int256 x, int256 y)`, breaking the invariant that one arithmetic expression must produce the value pushed to an external gauge and producing Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the voter supplies offsetting positive and negative deltas that net to zero, then assert `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` end identical in both runs.
