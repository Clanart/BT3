# Q5903: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
In wombat/WombatBribeManager.sol, castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Starting from a state where the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `getVoteForLp(lp) from the Wombat voter` inconsistent with `poolInfos[lp].totalVoteInVlmgp`, violating the invariant that a recorded cadence variable must actually gate the operation it appears to pace and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has just cancelled a cooldown so getUserVotable jumped upward, then assert `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` end identical in both runs.
