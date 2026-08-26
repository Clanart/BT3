# Q3998: BaseRewardPoolV2.donateRewards - donation front-run of a legitimate queueNewRewards

## Question
In rewards/BaseRewardPoolV2.sol, an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while the reward token charges a transfer fee so the received balance is below the requested amount, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `rewards[_rewardToken].rewardPerTokenStored` - breaking the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under the reward token charges a transfer fee so the received balance is below the requested amount, asserting at the end that `rewards[_rewardToken].queuedRewards` still equals `rewards[_rewardToken].rewardPerTokenStored` and the PoC's balance delta is non-positive.
