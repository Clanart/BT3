# Q4061: mWOMSVBaseRewarder.getRewards - forfeited value recycled to the same stakers who forfeited it

## Question
rewards/mWOMSVBaseRewarder.sol: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Under the attacker locks one block before a known large settlement and unlocks one block after, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `forfeitAmount` unreconciled with `rewardInfo.rewardPerTokenStored`, violates the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the attacker locks one block before a known large settlement and unlocks one block after, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account.
