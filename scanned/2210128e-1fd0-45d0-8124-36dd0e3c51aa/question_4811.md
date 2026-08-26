# Q4811: mWOMSVBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
rewards/mWOMSVBaseRewarder.sol - _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under a registered reward token has begun reverting on transfer, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` and the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a registered reward token has begun reverting on transfer, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.
