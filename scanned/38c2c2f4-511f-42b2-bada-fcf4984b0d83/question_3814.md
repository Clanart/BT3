# Q3814: vlMGPBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while totalStaked is zero and queuedRewards holds a backlog, and drive `balanceOf(account)` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` - breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish totalStaked is zero and queuedRewards holds a backlog, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
