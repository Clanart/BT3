# Q0193: mWOMSVBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Assuming the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(mWOMSV).totalSupply()` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, asserting at the end that `totalStaked()` still equals `IERC20(mWOMSV).totalSupply()` and the PoC's balance delta is non-positive.
