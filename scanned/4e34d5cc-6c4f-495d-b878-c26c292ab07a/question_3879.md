# Q3879: mWOMSVBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
In rewards/mWOMSVBaseRewarder.sol, _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while totalStaked is zero and queuedRewards holds a backlog, and drive `forfeitAmount` out of agreement with `rewardInfo.rewardPerTokenStored` - breaking the invariant that the reward index may only be raised against tokens the contract has actually retained - for Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up totalStaked is zero and queuedRewards holds a backlog, snapshot `forfeitAmount` and `rewardInfo.rewardPerTokenStored`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
