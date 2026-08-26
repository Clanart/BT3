# Q0626: vlMGPBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
In rewards/vlMGPBaseRewarder.sol, _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Starting from a state where the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `forfeitAmount` inconsistent with `rewardInfo.rewardPerTokenStored`, violating the invariant that the reward index may only be raised against tokens the contract has actually retained and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
