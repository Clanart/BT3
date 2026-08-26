# Q5737: WombatBribeManager.castVotes - harvestSinglePool drains pending bribes with no caller fee

## Question
Note that in wombat/WombatBribeManager.sol, harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Can an attacker holding only tokens bought on market reach it via `castVotes(bool swapForBnb)` under the bribe contract for the pool registers more than one reward token and force `getVoteForLp(lp) from the Wombat voter` apart from `poolInfos[lp].totalVoteInVlmgp`, breaking the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for the pool registers more than one reward token, then assert `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` end identical in both runs.
