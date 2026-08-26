# Q0533: vlMGPBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
In rewards/vlMGPBaseRewarder.sol, _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, and drive `forfeitAmount` out of agreement with `rewardInfo.rewardPerTokenStored` - breaking the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account.
