# Q3390: vlMGPBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
rewards/vlMGPBaseRewarder.sol: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `userRewards[_rewardToken][account]` unreconciled with `rewards[_rewardToken].rewardPerTokenStored`, violates the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting on every row that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose.
