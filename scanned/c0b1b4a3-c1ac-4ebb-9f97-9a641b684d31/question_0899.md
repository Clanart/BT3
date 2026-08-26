# Q0899: SignedSafeMath.wmul - targetVote minus currentVote is computed on two branches

## Question
In libraries/SignedSafeMath.sol, castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Starting from a state where targetVote is above currentVote so the first branch of castVotes runs, can an unprivileged EOA use `wmul(int256 x, int256 y)` to leave `int256(targetVote) - int256(currentVote)` inconsistent with `the uint256 votes pushed into the Wombat voter`, violating the invariant that one arithmetic expression must produce the value pushed to an external gauge and extracting Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: targetVote is above currentVote so the first branch of castVotes runs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at) under targetVote is above currentVote so the first branch of castVotes runs, asserting on every row that one arithmetic expression must produce the value pushed to an external gauge.
