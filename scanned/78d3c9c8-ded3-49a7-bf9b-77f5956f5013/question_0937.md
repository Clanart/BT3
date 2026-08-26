# Q0937: mWOMSVBaseRewarder.getRewards - forfeit erased by settling during cooldown

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the account's slot matured recently so the percent has only just begun to decay, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `totalStaked()` and `IERC20(mWOMSV).totalSupply()` no longer reconcile, violating the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() multiplies the pending amount by mWOMSV.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the account's slot matured recently so the percent has only just begun to decay, asserting on every row that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose.
