# Q2770: mWOMSVBaseRewarder.getRewards - forfeit erased by settling during cooldown

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Assuming a large MGP distribution has just been queued and no account has settled yet, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large MGP distribution has just been queued and no account has settled yet, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `rewards[_rewardToken].historicalRewards` equals `IERC20(_rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
