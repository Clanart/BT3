# Q0434: SignedSafeMath.toUint256 - targetVote minus currentVote is computed on two branches

## Question
In libraries/SignedSafeMath.sol, castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Starting from a state where the voter supplies the same pool several times with alternating signs, can an unprivileged EOA use `toUint256(int256 value)` to leave `int256 delta supplied by the voter` inconsistent with `uint256 pool.totalVoteInVlmgp`, violating the invariant that one arithmetic expression must produce the value pushed to an external gauge and extracting Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the voter supplies the same pool several times with alternating signs, call `toUint256(int256 value)`, and assert `int256 delta supplied by the voter` equals `uint256 pool.totalVoteInVlmgp` and that no account can withdraw more than it put in.
