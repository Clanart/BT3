# Q1924: mWOMSVBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
rewards/mWOMSVBaseRewarder.sol: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Under the computed forfeit lands just below the _amount / 1000 dust threshold, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `_calExpireForfeit(account,_amount)` unreconciled with `mWOMSV.getRewardablePercentWAD(account)`, violates the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the computed forfeit lands just below the _amount / 1000 dust threshold, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account.
