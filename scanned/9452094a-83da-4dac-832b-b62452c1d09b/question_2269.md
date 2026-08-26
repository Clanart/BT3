# Q2269: mWOMSVBaseRewarder.getRewards - forfeited value recycled to the same stakers who forfeited it

## Question
rewards/mWOMSVBaseRewarder.sol: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Under the computed forfeit lands just above the _amount / 1000 dust threshold, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `_calExpireForfeit(account,_amount)` unreconciled with `mWOMSV.getRewardablePercentWAD(account)`, violates the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just above the _amount / 1000 dust threshold, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `_calExpireForfeit(account,_amount)` versus `mWOMSV.getRewardablePercentWAD(account)` relation are unchanged by the attacker's transaction.
