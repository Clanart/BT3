# Q2268: vlMGPBaseRewarder.getRewards - forfeited value recycled to the same stakers who forfeited it

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `_calExpireForfeit(account,_amount)` apart from `vlMGP.getRewardablePercentWAD(account)`, breaking the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the computed forfeit lands just above the _amount / 1000 dust threshold, asserting at the end that `_calExpireForfeit(account,_amount)` still equals `vlMGP.getRewardablePercentWAD(account)` and the PoC's balance delta is non-positive.
