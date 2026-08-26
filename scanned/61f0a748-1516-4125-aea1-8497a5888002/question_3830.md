# Q3830: vlMGPBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
rewards/vlMGPBaseRewarder.sol: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and totalStaked is zero and queuedRewards holds a backlog, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `forfeitAmount` and `rewardInfo.rewardPerTokenStored` no longer reconcile, violating the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalStaked is zero and queuedRewards holds a backlog, call `getReward(address _account, address _receiver)`, and assert `forfeitAmount` equals `rewardInfo.rewardPerTokenStored` and that no account can withdraw more than it put in.
