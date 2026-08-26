# Q0636: WombatBribeManager.castVotes - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` no longer reconcile, violating the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, then assert `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` end identical in both runs.
