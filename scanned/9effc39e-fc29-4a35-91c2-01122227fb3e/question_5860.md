# Q5860: MasterMagpie.multiclaim - classification collision between vlmgp, MPGRewardPool and the default branch

## Question
Consider rewards/MasterMagpie.sol, where _multiClaim() buckets a pool by _stakingToken == address(vlmgp), then MPGRewardPool[_stakingToken], then default, and the three buckets are paid through three different mechanisms (queueMGP with forfeit, plain safeTransfer, forced vlMGP lock), so a pool that is misclassified pays MGP under the wrong forfeit and lock rules. Assuming the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, can an unprivileged attacker turn this into a divergence between `IBaseRewardPool(rewarder).balanceOf(user)` and `IBaseRewardPool(rewarder).totalStaked()` via `multiclaim(address[] _stakingTokens)`, breaking the invariant that the payout mechanism for a pool must be a single deterministic property of that pool and must not be reachable through an attacker-chosen array position and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: classification collision between vlmgp, MPGRewardPool and the default branch)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() buckets a pool by _stakingToken == address(vlmgp), then MPGRewardPool[_stakingToken], then default, and the three buckets are paid through three different mechanisms (queueMGP with forfeit, plain safeTransfer, forced vlMGP lock), so a pool that is misclassified pays MGP under the wrong forfeit and lock rules. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: the payout mechanism for a pool must be a single deterministic property of that pool and must not be reachable through an attacker-chosen array position; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, then assert `IBaseRewardPool(rewarder).balanceOf(user)` and `IBaseRewardPool(rewarder).totalStaked()` end identical in both runs.
