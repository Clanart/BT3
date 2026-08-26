# Q0620: SignedSafeMath.wmul - targetVote minus currentVote is computed on two branches

## Question
libraries/SignedSafeMath.sol - castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Can an unprivileged attacker controlling the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at, under targetVote is below currentVote so the second branch of castVotes runs, exploit this through `wmul(int256 x, int256 y)` to break the reconciliation between `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` and the invariant that one arithmetic expression must produce the value pushed to an external gauge, yielding Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at) under targetVote is below currentVote so the second branch of castVotes runs, asserting on every row that one arithmetic expression must produce the value pushed to an external gauge.
