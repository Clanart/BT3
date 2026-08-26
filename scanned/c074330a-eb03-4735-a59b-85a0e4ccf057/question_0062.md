# Q0062: SignedSafeMath.wmul - targetVote minus currentVote is computed on two branches

## Question
Note that in libraries/SignedSafeMath.sol, castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Can an attacker holding only tokens bought on market reach it via `wmul(int256 x, int256 y)` under the voter supplies offsetting positive and negative deltas that net to zero and force `int256(targetVote) - int256(currentVote)` apart from `the uint256 votes pushed into the Wombat voter`, breaking the invariant that one arithmetic expression must produce the value pushed to an external gauge for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at) under the voter supplies offsetting positive and negative deltas that net to zero, asserting on every row that one arithmetic expression must produce the value pushed to an external gauge.
