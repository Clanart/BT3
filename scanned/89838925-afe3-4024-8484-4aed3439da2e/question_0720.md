# Q0720: mWOMSVBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
In rewards/mWOMSVBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, so that `totalStaked()` diverges from `IERC20(mWOMSV).totalSupply()`, the invariant that total forfeit must be invariant to how a user splits their settlements is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `totalStaked()` and `IERC20(mWOMSV).totalSupply()` end identical in both runs.
