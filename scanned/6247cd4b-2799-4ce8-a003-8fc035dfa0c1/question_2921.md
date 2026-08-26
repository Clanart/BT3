# Q2921: vlMGPBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
In rewards/vlMGPBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under a large MGP distribution has just been queued and no account has settled yet, so that `rewards[_rewardToken].historicalRewards` diverges from `IERC20(_rewardToken).balanceOf(address(this))`, the invariant that total forfeit must be invariant to how a user splits their settlements is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large MGP distribution has just been queued and no account has settled yet, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.
