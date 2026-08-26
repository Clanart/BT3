# Q3281: BribeRewardPool.donateRewards - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` while the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `totalSupply at the moment of the flush` - breaking the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`: constrain the setup so that the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, fuzz the attacker inputs (_amountReward and which already-registered bribe token is provisioned), and assert after every call that a backlog accrued with no voters must not be assignable to a single one-block voter.
