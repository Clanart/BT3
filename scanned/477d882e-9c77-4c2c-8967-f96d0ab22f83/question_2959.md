# Q2959: vlMGPBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while a large MGP distribution has just been queued and no account has settled yet, and drive `_calExpireForfeit(account,_amount)` out of agreement with `vlMGP.getRewardablePercentWAD(account)` - breaking the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large MGP distribution has just been queued and no account has settled yet, call `getReward(address _account, address _receiver)`, and assert `_calExpireForfeit(account,_amount)` equals `vlMGP.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
