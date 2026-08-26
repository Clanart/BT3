# Q4031: mWOMSVBaseRewarder.getRewards - forfeit erased by settling during cooldown

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Under the attacker locks one block before a known large settlement and unlocks one block after, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `totalStaked()` unreconciled with `IERC20(mWOMSV).totalSupply()`, violates the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks one block before a known large settlement and unlocks one block after, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(mWOMSV).totalSupply()` relation are unchanged by the attacker's transaction.
