# Q0409: vlMGPBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
In rewards/vlMGPBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, so that `userRewards[_rewardToken][account]` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that total forfeit must be invariant to how a user splits their settlements is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, snapshot `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored`, run the attacker's `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
