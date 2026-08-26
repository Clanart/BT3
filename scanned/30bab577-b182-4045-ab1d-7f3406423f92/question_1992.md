# Q1992: vlMGPBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
rewards/vlMGPBaseRewarder.sol: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that the reward index may only be raised against tokens the contract has actually retained and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the computed forfeit lands just below the _amount / 1000 dust threshold, asserting on every row that the reward index may only be raised against tokens the contract has actually retained.
