# Q4195: mWOMSVBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the attacker locks one block before a known large settlement and unlocks one block after, so that `forfeitAmount` diverges from `rewardInfo.rewardPerTokenStored`, the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the attacker locks one block before a known large settlement and unlocks one block after, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
