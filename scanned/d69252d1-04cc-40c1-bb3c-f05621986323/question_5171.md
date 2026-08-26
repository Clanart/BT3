# Q5171: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
wombat/WombatBribeManager.sol - the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the attacker passes the same lp address several times in one array, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` and the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker passes the same lp address several times in one array, then assert `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` end identical in both runs.
