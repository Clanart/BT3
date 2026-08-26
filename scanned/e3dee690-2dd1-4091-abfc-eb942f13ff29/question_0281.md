# Q0281: BaseRewardPool.donateRewards - donation front-run of a legitimate queueNewRewards

## Question
In rewards/BaseRewardPool.sol, an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Starting from a state where the pool has exactly one registered reward token and no queued backlog, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violating the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the pool has exactly one registered reward token and no queued backlog, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block.
