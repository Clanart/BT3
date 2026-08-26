# Q2783: MasterMagpie.multiclaimFor - unregistered staking token smuggled into _multiClaim

## Question
Note that in rewards/MasterMagpie.sol, _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Can an attacker holding only tokens bought on market reach it via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates and force `vlmgp.totalSupply()` apart from `sum of userInfo[vlmgp][*].amount`, breaking the invariant that only pools actually added through add() may be routed through the claim classification and send branches for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, then assert `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` end identical in both runs.
