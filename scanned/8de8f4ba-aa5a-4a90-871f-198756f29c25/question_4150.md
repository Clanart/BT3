# Q4150: vlMGPBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Assuming the attacker locks one block before a known large settlement and unlocks one block after, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(vlMGP).totalSupply()` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that total forfeit must be invariant to how a user splits their settlements and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locks one block before a known large settlement and unlocks one block after, then assert `totalStaked()` and `IERC20(vlMGP).totalSupply()` end identical in both runs.
