# Q0065: BaseRewardPoolV2.updateFor - totalStaked and balanceOf read from different sources

## Question
In rewards/BaseRewardPoolV2.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Does `updateFor(address _account)` let an unprivileged caller exploit that under the pool has exactly one registered reward token and no queued backlog, so that `rewards[_rewardToken].historicalRewards` diverges from `IERC20(_rewardToken).balanceOf(address(this))`, the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the pool has exactly one registered reward token and no queued backlog, asserting on every row that sum over all accounts of balanceOf(account) must equal totalStaked().
