# Q4988: mWOMSVBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, so that `_calExpireForfeit(account,_amount)` diverges from `mWOMSV.getRewardablePercentWAD(account)`, the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker settles the same reward token through two separate multiclaimSpec calls in one block, snapshot `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)`, run the attacker's `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
