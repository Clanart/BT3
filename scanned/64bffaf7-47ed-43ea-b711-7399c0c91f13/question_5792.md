# Q5792: MasterMagpie.multiclaimSpec - unregistered staking token smuggled into _multiClaim

## Question
Note that in rewards/MasterMagpie.sol, _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Can an attacker holding only tokens bought on market reach it via `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` under the victim has a large unClaimedMgp balance that has not been settled for several epochs and force `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` apart from `block.timestamp`, breaking the invariant that only pools actually added through add() may be routed through the claim classification and send branches for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unClaimedMgp balance that has not been settled for several epochs, then assert `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` end identical in both runs.
