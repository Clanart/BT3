# Q2808: mWOMSVBaseRewarder.getRewards - forfeited value recycled to the same stakers who forfeited it

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Assuming a large MGP distribution has just been queued and no account has settled yet, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under a large MGP distribution has just been queued and no account has settled yet, asserting on every row that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account.
