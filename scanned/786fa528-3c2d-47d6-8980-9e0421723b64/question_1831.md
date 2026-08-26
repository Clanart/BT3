# Q1831: vlMGPBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
In rewards/vlMGPBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Starting from a state where the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violating the invariant that total forfeit must be invariant to how a user splits their settlements and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just below the _amount / 1000 dust threshold, then assert `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` end identical in both runs.
