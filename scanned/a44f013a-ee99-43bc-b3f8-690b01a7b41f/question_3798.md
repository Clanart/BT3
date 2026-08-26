# Q3798: vlMGPBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
rewards/vlMGPBaseRewarder.sol: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Under totalStaked is zero and queuedRewards holds a backlog, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `totalStaked()` unreconciled with `IERC20(vlMGP).totalSupply()`, violates the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that totalStaked is zero and queuedRewards holds a backlog, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose.
