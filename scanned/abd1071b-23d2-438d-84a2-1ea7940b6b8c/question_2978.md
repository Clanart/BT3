# Q2978: mWOMSVBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
rewards/mWOMSVBaseRewarder.sol - _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under a large MGP distribution has just been queued and no account has settled yet, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a large MGP distribution has just been queued and no account has settled yet, snapshot `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
