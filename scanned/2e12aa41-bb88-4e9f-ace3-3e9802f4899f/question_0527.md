# Q0527: SignedSafeMath.wdiv - targetVote minus currentVote is computed on two branches

## Question
libraries/SignedSafeMath.sol: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. With the target minus current arithmetic inside WombatBribeManager.castVotes under attacker control and the voter supplies the same pool several times with alternating signs, can an unprivileged caller sequence `wdiv(int256 x, int256 y)` so that `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` no longer reconcile, violating the invariant that one arithmetic expression must produce the value pushed to an external gauge and realising Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the voter supplies the same pool several times with alternating signs, then assert `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` end identical in both runs.
