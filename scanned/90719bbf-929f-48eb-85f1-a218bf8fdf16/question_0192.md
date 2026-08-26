# Q0192: vlMGPBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
rewards/vlMGPBaseRewarder.sol: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `totalStaked()` and `IERC20(vlMGP).totalSupply()` no longer reconcile, violating the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `totalStaked()` and `IERC20(vlMGP).totalSupply()` end identical in both runs.
