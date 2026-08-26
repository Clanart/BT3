# Q2499: mWOMSVBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `userRewards[_rewardToken][account]` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under the computed forfeit lands just above the _amount / 1000 dust threshold, asserting at the end that `userRewards[_rewardToken][account]` still equals `rewards[_rewardToken].rewardPerTokenStored` and the PoC's balance delta is non-positive.
