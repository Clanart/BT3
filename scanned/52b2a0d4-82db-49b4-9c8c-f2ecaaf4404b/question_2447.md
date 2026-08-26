# Q2447: MasterMagpie.multiclaim - unregistered staking token smuggled into _multiClaim

## Question
rewards/MasterMagpie.sol - _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Can an unprivileged attacker controlling the full _stakingTokens array, including duplicates and unregistered addresses, under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, exploit this through `multiclaim(address[] _stakingTokens)` to break the reconciliation between `IBaseRewardPool(rewarder).balanceOf(user)` and `IBaseRewardPool(rewarder).totalStaked()` and the invariant that only pools actually added through add() may be routed through the claim classification and send branches, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, then assert `IBaseRewardPool(rewarder).balanceOf(user)` and `IBaseRewardPool(rewarder).totalStaked()` end identical in both runs.
