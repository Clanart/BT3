# Q4391: mWOMSVBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while the victim has not settled for several epochs and holds a large userRewards balance, and drive `forfeitAmount` out of agreement with `rewardInfo.rewardPerTokenStored` - breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has not settled for several epochs and holds a large userRewards balance, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
