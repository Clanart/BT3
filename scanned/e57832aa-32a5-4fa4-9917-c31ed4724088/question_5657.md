# Q5657: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
Consider wombat/WombatBribeManager.sol, where the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Assuming the attacker passes an lp address that was never registered in poolInfos, can an unprivileged attacker turn this into a divergence between `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` via `harvestSinglePool(address[] _lps)`, breaking the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes an lp address that was never registered in poolInfos, call `harvestSinglePool(address[] _lps)`, and assert `earnedRewards reported by claimAllBribes` equals `the tokens actually transferred by getReward` and that no account can withdraw more than it put in.
