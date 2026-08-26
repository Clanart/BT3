# Q3264: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Does `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` let an unprivileged caller exploit that under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, so that `rewards[_rewardToken].rewardPerTokenStored` diverges from `userRewardPerTokenPaid[_rewardToken][account]`, the invariant that only the vote-casting path may move a gauge's bribe index is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence atomically under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, asserting at the end that `rewards[_rewardToken].rewardPerTokenStored` still equals `userRewardPerTokenPaid[_rewardToken][account]` and the PoC's balance delta is non-positive.
