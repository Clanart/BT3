# Q5837: WombatBribeManager.harvestSinglePool - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol - harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the victim has a large unsettled balance in the pool rewarder, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` and the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)) under the victim has a large unsettled balance in the pool rewarder, asserting on every row that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge.
