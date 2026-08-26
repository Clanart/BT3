# Q5709: MasterMagpie.multiclaimSpec - unregistered staking token smuggled into _multiClaim

## Question
In rewards/MasterMagpie.sol, _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Starting from a state where the contract is paused so only emergencyWithdraw is reachable, can an unprivileged EOA use `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` to leave `_calLpSupply(_stakingToken)` inconsistent with `IERC20(_stakingToken).balanceOf(masterMagpie)`, violating the invariant that only pools actually added through add() may be routed through the claim classification and send branches and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract is paused so only emergencyWithdraw is reachable, then assert `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)` end identical in both runs.
