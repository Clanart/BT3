# Q4570: vlMGPBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
In rewards/vlMGPBaseRewarder.sol, _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Starting from a state where the victim has not settled for several epochs and holds a large userRewards balance, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `_calExpireForfeit(account,_amount)` inconsistent with `vlMGP.getRewardablePercentWAD(account)`, violating the invariant that the reward index may only be raised against tokens the contract has actually retained and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the victim has not settled for several epochs and holds a large userRewards balance, snapshot `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
