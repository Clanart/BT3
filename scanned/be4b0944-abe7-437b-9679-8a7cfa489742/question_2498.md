# Q2498: vlMGPBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
In rewards/vlMGPBaseRewarder.sol, _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the computed forfeit lands just above the _amount / 1000 dust threshold, so that `userRewards[_rewardToken][account]` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just above the _amount / 1000 dust threshold, then assert `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
