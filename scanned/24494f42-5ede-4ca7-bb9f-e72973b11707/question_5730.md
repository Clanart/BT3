# Q5730: MasterMagpie.multiclaimFor - unregistered staking token smuggled into _multiClaim

## Question
rewards/MasterMagpie.sol: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. With _account (any victim), the staking-token list and the per-pool reward-token lists under attacker control and the contract is paused so only emergencyWithdraw is reachable, can an unprivileged caller sequence `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` so that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` no longer reconcile, violating the invariant that only pools actually added through add() may be routed through the claim classification and send branches and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract is paused so only emergencyWithdraw is reachable, then assert `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` end identical in both runs.
