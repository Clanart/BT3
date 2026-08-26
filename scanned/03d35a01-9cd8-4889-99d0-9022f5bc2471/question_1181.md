# Q1181: vlMGPBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
rewards/vlMGPBaseRewarder.sol: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the account's slot matured recently so the percent has only just begun to decay, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `totalStaked()` and `IERC20(vlMGP).totalSupply()` no longer reconcile, violating the invariant that total forfeit must be invariant to how a user splits their settlements and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account's slot matured recently so the percent has only just begun to decay, then assert `totalStaked()` and `IERC20(vlMGP).totalSupply()` end identical in both runs.
