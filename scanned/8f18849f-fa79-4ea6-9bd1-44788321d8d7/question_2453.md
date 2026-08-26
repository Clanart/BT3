# Q2453: mWOMSVBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just above the _amount / 1000 dust threshold, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `rewards[_rewardToken].historicalRewards` versus `IERC20(_rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
