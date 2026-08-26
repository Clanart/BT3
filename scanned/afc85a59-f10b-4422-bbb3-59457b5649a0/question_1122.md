# Q1122: mWOMSVBaseRewarder.getRewards - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Assuming the account's slot matured recently so the percent has only just begun to decay, can an unprivileged attacker turn this into a divergence between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the reward index may only be raised against tokens the contract has actually retained and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account's slot matured recently so the percent has only just begun to decay, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
