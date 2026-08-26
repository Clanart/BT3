# Q3630: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
wombat/WombatBribeManager.sol: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Under the pool the attacker voted for has been deactivated so unvote reverts, is there an unprivileged sequence of `harvestSinglePool(address[] _lps)` that leaves `userVotedForPoolInVlmgp[user][lp]` unreconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violates the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)) under the pool the attacker voted for has been deactivated so unvote reverts, asserting on every row that harvesting one pool twice in a call must be equivalent to harvesting it once.
