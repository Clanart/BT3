# Q1428: BaseRewardPoolV2.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPoolV2.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored, and drive `rewardTokens.length` out of agreement with `isRewardToken[_rewardToken]` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `rewardTokens.length` versus `isRewardToken[_rewardToken]` relation are unchanged by the attacker's transaction.
