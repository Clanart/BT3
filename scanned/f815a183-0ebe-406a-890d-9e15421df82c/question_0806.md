# Q0806: SignedSafeMath.wdiv - targetVote minus currentVote is computed on two branches

## Question
In libraries/SignedSafeMath.sol, castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Can an unprivileged attacker reach this through `wdiv(int256 x, int256 y)` while targetVote is below currentVote so the second branch of castVotes runs, and drive `totalUserVote accumulated as int256` out of agreement with `userTotalVotedInVlmgp as uint256` - breaking the invariant that one arithmetic expression must produce the value pushed to an external gauge - for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: targetVote minus currentVote is computed on two branches)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: castVotes computes votes[i] as int256(targetVote - currentVote) on one branch and int256(targetVote) - int256(currentVote) on the other, so two different arithmetic orders produce the value pushed to the Wombat voter. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: one arithmetic expression must produce the value pushed to an external gauge; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under targetVote is below currentVote so the second branch of castVotes runs, then assert `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` end identical in both runs.
