# Q2960: mWOMSVBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and a large MGP distribution has just been queued and no account has settled yet, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that a large MGP distribution has just been queued and no account has settled yet, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose.
