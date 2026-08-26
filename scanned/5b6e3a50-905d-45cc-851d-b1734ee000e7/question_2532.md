# Q2532: BribeRewardPool.donateRewards - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Starting from a state where the bribe token has begun reverting on transfer, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` to leave `rewards[_rewardToken].rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the bribe token has begun reverting on transfer, snapshot `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
