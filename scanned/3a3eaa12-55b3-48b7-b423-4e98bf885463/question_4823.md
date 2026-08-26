# Q4823: mWOMSVBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and a registered reward token has begun reverting on transfer, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that a registered reward token has begun reverting on transfer, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
