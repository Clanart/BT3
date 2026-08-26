# Q5064: vlMGPBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
rewards/vlMGPBaseRewarder.sol - _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor, under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` and the invariant that total forfeit must be invariant to how a user splits their settlements, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.
