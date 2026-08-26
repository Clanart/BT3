# Q3357: mWOMSVBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
rewards/mWOMSVBaseRewarder.sol: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that total forfeit must be invariant to how a user splits their settlements and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting on every row that total forfeit must be invariant to how a user splits their settlements.
