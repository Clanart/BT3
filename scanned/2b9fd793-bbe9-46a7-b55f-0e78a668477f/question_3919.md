# Q3919: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
rewards/BribeRewardPool.sol - BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Can an unprivileged attacker controlling _amountReward and which already-registered bribe token is provisioned, under the victim has a large unsettled bribe balance, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that only the vote-casting path may move a gauge's bribe index, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unsettled bribe balance, then assert `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` end identical in both runs.
