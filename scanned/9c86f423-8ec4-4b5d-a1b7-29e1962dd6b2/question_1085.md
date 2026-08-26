# Q1085: SignedSafeMath.wdiv - targetVote minus currentVote is computed on two branches

## Question
libraries/SignedSafeMath.sol: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Under targetVote is above currentVote so the first branch of castVotes runs, is there an unprivileged sequence of `wdiv(int256 x, int256 y)` that leaves `int256 delta supplied by the voter` unreconciled with `uint256 pool.totalVoteInVlmgp`, violates the invariant that one arithmetic expression must produce the value pushed to an external gauge, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: targetVote is above currentVote so the first branch of castVotes runs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under targetVote is above currentVote so the first branch of castVotes runs, then assert `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` end identical in both runs.
