# Q1010: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
rewards/BribeRewardPool.sol: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Under the attacker votes and casts inside one transaction through voteAndCast, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` that leaves `rewards[_rewardToken].rewardPerTokenStored` unreconciled with `userRewardPerTokenPaid[_rewardToken][account]`, violates the invariant that only the vote-casting path may move a gauge's bribe index, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward and which already-registered bribe token is provisioned) under the attacker votes and casts inside one transaction through voteAndCast, asserting on every row that only the vote-casting path may move a gauge's bribe index.
