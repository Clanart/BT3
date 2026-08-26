# Q1572: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
rewards/BribeRewardPool.sol: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. With _amountReward and which already-registered bribe token is provisioned under attacker control and totalSupply is zero because every voter has unvoted, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that only the vote-casting path may move a gauge's bribe index and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`: constrain the setup so that totalSupply is zero because every voter has unvoted, fuzz the attacker inputs (_amountReward and which already-registered bribe token is provisioned), and assert after every call that only the vote-casting path may move a gauge's bribe index.
