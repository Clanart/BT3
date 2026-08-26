# Q2788: vlMGPBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under a large MGP distribution has just been queued and no account has settled yet, so that `_calExpireForfeit(account,_amount)` diverges from `vlMGP.getRewardablePercentWAD(account)`, the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under a large MGP distribution has just been queued and no account has settled yet, asserting on every row that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
