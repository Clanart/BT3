# Q1993: mWOMSVBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Assuming the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged attacker turn this into a divergence between `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` via `getReward(address _account, address _receiver)`, breaking the invariant that the reward index may only be raised against tokens the contract has actually retained and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the computed forfeit lands just below the _amount / 1000 dust threshold, snapshot `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
