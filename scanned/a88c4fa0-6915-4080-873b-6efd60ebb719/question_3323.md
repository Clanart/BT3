# Q3323: mWOMSVBaseRewarder.getRewards - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
In rewards/mWOMSVBaseRewarder.sol, _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Starting from a state where the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `totalStaked()` inconsistent with `IERC20(mWOMSV).totalSupply()`, violating the invariant that the reward index may only be raised against tokens the contract has actually retained and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, then assert `totalStaked()` and `IERC20(mWOMSV).totalSupply()` end identical in both runs.
