# Q1878: mWOMSVBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Under the computed forfeit lands just below the _amount / 1000 dust threshold, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `forfeitAmount` unreconciled with `rewardInfo.rewardPerTokenStored`, violates the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just below the _amount / 1000 dust threshold, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
