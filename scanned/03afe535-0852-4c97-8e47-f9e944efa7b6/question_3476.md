# Q3476: mWOMSVBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
In rewards/mWOMSVBaseRewarder.sol, _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, so that `balanceOf(account)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, the invariant that the reward index may only be raised against tokens the contract has actually retained is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that the reward index may only be raised against tokens the contract has actually retained.
