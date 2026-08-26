# Q0713: SignedSafeMath.toUint256 - targetVote minus currentVote is computed on two branches

## Question
Consider libraries/SignedSafeMath.sol, where castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Assuming targetVote is below currentVote so the second branch of castVotes runs, can an unprivileged attacker turn this into a divergence between `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` via `toUint256(int256 value)`, breaking the invariant that one arithmetic expression must produce the value pushed to an external gauge and producing Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange targetVote is below currentVote so the second branch of castVotes runs, call `toUint256(int256 value)`, and assert `int256(targetVote) - int256(currentVote)` equals `the uint256 votes pushed into the Wombat voter` and that no account can withdraw more than it put in.
