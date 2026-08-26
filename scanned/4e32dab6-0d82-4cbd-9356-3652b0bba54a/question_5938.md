# Q5938: MasterMagpie.multiclaim - unregistered staking token smuggled into _multiClaim

## Question
rewards/MasterMagpie.sol: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Under the attacker repeats the call in the same block to observe the second, no-op iteration, is there an unprivileged sequence of `multiclaim(address[] _stakingTokens)` that leaves `IBaseRewardPool(rewarder).balanceOf(user)` unreconciled with `IBaseRewardPool(rewarder).totalStaked()`, violates the invariant that only pools actually added through add() may be routed through the claim classification and send branches, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaim(address[] _stakingTokens)` sequence atomically under the attacker repeats the call in the same block to observe the second, no-op iteration, asserting at the end that `IBaseRewardPool(rewarder).balanceOf(user)` still equals `IBaseRewardPool(rewarder).totalStaked()` and the PoC's balance delta is non-positive.
