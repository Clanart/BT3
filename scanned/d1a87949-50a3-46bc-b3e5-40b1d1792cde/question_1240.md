# Q1240: mWOMSVBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the account's slot matured recently so the percent has only just begun to decay, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` no longer reconcile, violating the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account's slot matured recently so the percent has only just begun to decay, call `getReward(address _account, address _receiver)`, and assert `balanceOf(account)` equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` and that no account can withdraw more than it put in.
