# Q5778: MasterMagpie.multiclaim - unregistered staking token smuggled into _multiClaim

## Question
rewards/MasterMagpie.sol: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Under the victim has a large unClaimedMgp balance that has not been settled for several epochs, is there an unprivileged sequence of `multiclaim(address[] _stakingTokens)` that leaves `_calLpSupply(_stakingToken)` unreconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`, violates the invariant that only pools actually added through add() may be routed through the claim classification and send branches, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unClaimedMgp balance that has not been settled for several epochs, then assert `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)` end identical in both runs.
