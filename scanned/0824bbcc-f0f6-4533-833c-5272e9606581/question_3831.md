# Q3831: mWOMSVBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Assuming totalStaked is zero and queuedRewards holds a backlog, can an unprivileged attacker turn this into a divergence between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` via `getReward(address _account, address _receiver)`, breaking the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that totalStaked is zero and queuedRewards holds a backlog, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account.
