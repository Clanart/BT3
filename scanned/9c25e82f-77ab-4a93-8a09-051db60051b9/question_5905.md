# Q5905: WombatBribeManager.castVotes - castVotes writes lastCastTime before the external voter call

## Question
Note that in wombat/WombatBribeManager.sol, lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Can an attacker holding only tokens bought on market reach it via `castVotes(bool swapForBnb)` under the attacker has just cancelled a cooldown so getUserVotable jumped upward and force `poolInfos[lp].isActive` apart from `userVotedForPoolInVlmgp[user][lp]`, breaking the invariant that a cadence marker must only advance once the operation it marks has completed for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under the attacker has just cancelled a cooldown so getUserVotable jumped upward, asserting on every row that a cadence marker must only advance once the operation it marks has completed.
