# Q3799: mWOMSVBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while totalStaked is zero and queuedRewards holds a backlog, and drive `totalStaked()` out of agreement with `IERC20(mWOMSV).totalSupply()` - breaking the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under totalStaked is zero and queuedRewards holds a backlog, then assert `totalStaked()` and `IERC20(mWOMSV).totalSupply()` end identical in both runs.
