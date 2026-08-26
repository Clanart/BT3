# Q2758: WombatBribeManager.castVotes - castVotes writes lastCastTime before the external voter call

## Question
In wombat/WombatBribeManager.sol, lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Starting from a state where the attacker votes in the block immediately before a known keeper cast, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `poolInfos[lp].isActive` inconsistent with `userVotedForPoolInVlmgp[user][lp]`, violating the invariant that a cadence marker must only advance once the operation it marks has completed and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the attacker votes in the block immediately before a known keeper cast, snapshot `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
