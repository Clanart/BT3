# Q4571: mWOMSVBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
rewards/mWOMSVBaseRewarder.sol - _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under the victim has not settled for several epochs and holds a large userRewards balance, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` and the invariant that the reward index may only be raised against tokens the contract has actually retained, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has not settled for several epochs and holds a large userRewards balance, call `getReward(address _account, address _receiver)`, and assert `_calExpireForfeit(account,_amount)` equals `mWOMSV.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
