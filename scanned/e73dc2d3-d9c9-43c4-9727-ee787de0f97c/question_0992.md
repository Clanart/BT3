# Q0992: SignedSafeMath.toUint256 - targetVote minus currentVote is computed on two branches

## Question
libraries/SignedSafeMath.sol: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. With the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote under attacker control and targetVote is above currentVote so the first branch of castVotes runs, can an unprivileged caller sequence `toUint256(int256 value)` so that `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` no longer reconcile, violating the invariant that one arithmetic expression must produce the value pushed to an external gauge and realising Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: targetVote is above currentVote so the first branch of castVotes runs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange targetVote is above currentVote so the first branch of castVotes runs, call `toUint256(int256 value)`, and assert `totalUserVote accumulated as int256` equals `userTotalVotedInVlmgp as uint256` and that no account can withdraw more than it put in.
