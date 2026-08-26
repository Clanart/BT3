# Q1877: vlMGPBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the computed forfeit lands just below the _amount / 1000 dust threshold and force `forfeitAmount` apart from `rewardInfo.rewardPerTokenStored`, breaking the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the computed forfeit lands just below the _amount / 1000 dust threshold, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose.
