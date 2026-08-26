# Q4209: mWOMSVBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
rewards/mWOMSVBaseRewarder.sol - _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under the attacker locks one block before a known large settlement and unlocks one block after, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` and the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locks one block before a known large settlement and unlocks one block after, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.
