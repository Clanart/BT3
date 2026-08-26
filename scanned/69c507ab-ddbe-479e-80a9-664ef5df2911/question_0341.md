# Q0341: SignedSafeMath.wmul - targetVote minus currentVote is computed on two branches

## Question
In libraries/SignedSafeMath.sol, castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Does `wmul(int256 x, int256 y)` let an unprivileged caller exploit that under the voter supplies the same pool several times with alternating signs, so that `totalUserVote accumulated as int256` diverges from `userTotalVotedInVlmgp as uint256`, the invariant that one arithmetic expression must produce the value pushed to an external gauge is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at) under the voter supplies the same pool several times with alternating signs, asserting on every row that one arithmetic expression must produce the value pushed to an external gauge.
