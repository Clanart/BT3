# Q2996: mWOMSVBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
rewards/mWOMSVBaseRewarder.sol: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Under a large MGP distribution has just been queued and no account has settled yet, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `totalStaked()` unreconciled with `IERC20(mWOMSV).totalSupply()`, violates the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large MGP distribution has just been queued and no account has settled yet, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(mWOMSV).totalSupply()` relation are unchanged by the attacker's transaction.
